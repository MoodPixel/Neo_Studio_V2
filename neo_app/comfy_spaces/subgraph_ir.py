from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping, Sequence


SUBGRAPH_IR_SCHEMA_VERSION = "neo.comfy_space.subgraph_ir.v1"
_TOKEN_RE = re.compile(r"[^A-Za-z0-9_-]+")


class ComfySubgraphNormalizationError(ValueError):
    """Raised when a normal Comfy editor workflow cannot be flattened safely."""


@dataclass(frozen=True)
class _Binding:
    kind: str
    node_id: str = ""
    output_slot: int = 0
    value: Any = None

    @classmethod
    def link(cls, node_id: str, output_slot: int) -> "_Binding":
        return cls(kind="link", node_id=str(node_id), output_slot=int(output_slot))

    @classmethod
    def literal(cls, value: Any) -> "_Binding":
        return cls(kind="literal", value=deepcopy(value))

    @classmethod
    def missing(cls) -> "_Binding":
        return cls(kind="missing")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _link_record(raw_link: Any) -> dict[str, Any] | None:
    if isinstance(raw_link, Mapping):
        item = dict(raw_link)
        required = ("id", "origin_id", "origin_slot", "target_id", "target_slot")
        if not all(key in item for key in required):
            return None
        return {
            "id": str(item["id"]),
            "origin_id": str(item["origin_id"]),
            "origin_slot": item["origin_slot"],
            "target_id": str(item["target_id"]),
            "target_slot": item["target_slot"],
            "type": item.get("type"),
        }
    if isinstance(raw_link, Sequence) and not isinstance(raw_link, (str, bytes, bytearray)) and len(raw_link) >= 5:
        return {
            "id": str(raw_link[0]),
            "origin_id": str(raw_link[1]),
            "origin_slot": raw_link[2],
            "target_id": str(raw_link[3]),
            "target_slot": raw_link[4],
            "type": raw_link[5] if len(raw_link) > 5 else None,
        }
    return None


def _safe_fragment(value: Any, fallback: str = "node") -> str:
    text = _TOKEN_RE.sub("_", str(value or "").strip()).strip("_")
    return text[:72] or fallback


def _canonical_nested_node_id(scope_path: tuple[str, ...], local_id: str) -> str:
    if not scope_path:
        return str(local_id)
    raw = "/".join((*scope_path, str(local_id)))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    readable = "_".join(_safe_fragment(item, "sg") for item in (*scope_path, str(local_id)))
    return f"sg_{readable[:96]}_{digest}"


def _serializable_interface_type(raw_type: Any) -> bool:
    type_name = str(raw_type or "").strip().upper()
    return type_name in {"INT", "INTEGER", "FLOAT", "NUMBER", "DOUBLE", "STRING", "BOOLEAN", "BOOL", "COMBO"}


def _collect_definitions(root: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def walk(owner: Mapping[str, Any]) -> None:
        definitions = _mapping(owner.get("definitions"))
        subgraphs = definitions.get("subgraphs") if isinstance(definitions.get("subgraphs"), list) else []
        for raw in subgraphs:
            if not isinstance(raw, Mapping):
                continue
            item = deepcopy(dict(raw))
            definition_id = str(item.get("id") or "").strip()
            if not definition_id:
                raise ComfySubgraphNormalizationError("Comfy subgraph definition is missing its id.")
            existing = found.get(definition_id)
            if existing is not None and existing != item:
                raise ComfySubgraphNormalizationError(f"Comfy workflow contains conflicting subgraph definition id {definition_id}.")
            found[definition_id] = item
            walk(item)

    walk(root)
    return found


class _EditorSubgraphFlattener:
    def __init__(self, root: Mapping[str, Any], definitions: Mapping[str, Mapping[str, Any]]):
        self.root = deepcopy(dict(root))
        self.definitions = {str(key): deepcopy(dict(value)) for key, value in definitions.items()}
        self.flat_nodes: list[dict[str, Any]] = []
        self.flat_node_by_id: dict[str, dict[str, Any]] = {}
        self.flat_links: list[dict[str, Any]] = []
        self.instances: list[dict[str, Any]] = []
        self._instance_outputs: dict[tuple[str, ...], dict[int, _Binding]] = {}
        self._link_counter = 0
        self._definition_stack: list[str] = []
        self._used_definitions: set[str] = set()
        self._max_depth = 0
        self._modern_reroute_count = 0

    def flatten(self) -> tuple[dict[str, Any], dict[str, Any]]:
        self._expand_scope(self.root, scope_path=(), parent_bindings={}, definition_id="", definition_name="")
        result = deepcopy(self.root)
        result["nodes"] = self.flat_nodes
        result["links"] = self.flat_links
        result.pop("definitions", None)
        # Reroute points are visual metadata. Current Comfy save files already
        # keep direct origin/target endpoints on each link, so the executable
        # flattening does not need to synthesize reroute nodes.
        result["reroutes"] = []

        return result, {
            "schema_version": SUBGRAPH_IR_SCHEMA_VERSION,
            "flattened": bool(self.instances),
            "definition_count": len(self.definitions),
            "used_definition_count": len(self._used_definitions),
            "instance_count": len(self.instances),
            "max_depth": self._max_depth,
            "modern_reroute_count": self._modern_reroute_count,
            "instances": deepcopy(self.instances),
        }

    def _scope_nodes(self, scope: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        nodes = scope.get("nodes")
        if not isinstance(nodes, list):
            raise ComfySubgraphNormalizationError("Comfy graph scope must contain a nodes array.")
        result: dict[str, dict[str, Any]] = {}
        for raw_node in nodes:
            if not isinstance(raw_node, Mapping):
                raise ComfySubgraphNormalizationError("Comfy graph scope contains a non-object node entry.")
            node = deepcopy(dict(raw_node))
            node_id = str(node.get("id") if node.get("id") is not None else "").strip()
            if not node_id:
                raise ComfySubgraphNormalizationError("Comfy graph scope contains a node without an id.")
            if node_id in result:
                raise ComfySubgraphNormalizationError(f"Comfy graph scope contains duplicate node id {node_id}.")
            result[node_id] = node
        return result

    def _scope_links(self, scope: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        links = scope.get("links", [])
        if not isinstance(links, list):
            raise ComfySubgraphNormalizationError("Comfy graph scope links must be an array.")
        result: dict[str, dict[str, Any]] = {}
        for raw_link in links:
            parsed = _link_record(raw_link)
            if parsed is None:
                raise ComfySubgraphNormalizationError("Comfy graph scope contains an unsupported link record.")
            result[parsed["id"]] = parsed
        reroutes = scope.get("reroutes")
        if isinstance(reroutes, list):
            self._modern_reroute_count += len(reroutes)
        return result

    def _global_node_id(self, scope_path: tuple[str, ...], local_id: str) -> str:
        return _canonical_nested_node_id(scope_path, local_id)

    def _new_link_id(self, scope_path: tuple[str, ...], target_node_id: str, target_field: str) -> str:
        self._link_counter += 1
        path = "_".join(_safe_fragment(item, "sg") for item in scope_path) or "root"
        return f"neo_sg_{path}_{_safe_fragment(target_node_id)}_{_safe_fragment(target_field)}_{self._link_counter}"

    def _append_flat_node(
        self,
        node: Mapping[str, Any],
        *,
        scope_path: tuple[str, ...],
        definition_id: str,
        definition_name: str,
    ) -> dict[str, Any]:
        local_id = str(node.get("id"))
        global_id = self._global_node_id(scope_path, local_id)
        if global_id in self.flat_node_by_id:
            return self.flat_node_by_id[global_id]
        clone = deepcopy(dict(node))
        clone["id"] = global_id
        clone["_neo_subgraph"] = {
            "source_node_id": local_id,
            "scope_path": list(scope_path),
            "subgraph_depth": len(scope_path),
            "definition_id": definition_id,
            "definition_name": definition_name,
        }
        self.flat_nodes.append(clone)
        self.flat_node_by_id[global_id] = clone
        return clone

    def _set_literal_on_target(self, target: dict[str, Any], field: str, value: Any) -> None:
        named = target.get("widgets_values_named")
        if not isinstance(named, Mapping):
            named = {}
        else:
            named = deepcopy(dict(named))
        named[str(field)] = deepcopy(value)
        target["widgets_values_named"] = named

    def _expand_scope(
        self,
        scope: Mapping[str, Any],
        *,
        scope_path: tuple[str, ...],
        parent_bindings: Mapping[int, _Binding],
        definition_id: str,
        definition_name: str,
    ) -> dict[int, _Binding]:
        node_map = self._scope_nodes(scope)
        link_map = self._scope_links(scope)
        input_node_id = str(_mapping(scope.get("inputNode")).get("id") or "")
        output_node_id = str(_mapping(scope.get("outputNode")).get("id") or "")

        # Materialize every real node first so forward references have stable ids.
        for node_id, node in node_map.items():
            node_type = str(node.get("type") or "").strip()
            if node_type in self.definitions:
                continue
            self._append_flat_node(
                node,
                scope_path=scope_path,
                definition_id=definition_id,
                definition_name=definition_name,
            )

        instance_cache: dict[str, dict[int, _Binding]] = {}
        resolving_instances: set[str] = set()

        def resolve_source(source_id: str, output_slot: Any) -> _Binding:
            sid = str(source_id)
            try:
                slot = int(output_slot)
            except (TypeError, ValueError) as exc:
                raise ComfySubgraphNormalizationError(f"Subgraph source node {sid} has invalid output slot {output_slot}.") from exc

            if input_node_id and sid == input_node_id:
                return parent_bindings.get(slot, _Binding.missing())

            source = node_map.get(sid)
            if source is None:
                raise ComfySubgraphNormalizationError(f"Subgraph link references missing source node {sid}.")

            mode = source.get("mode", 0)
            node_type = str(source.get("type") or "").strip()
            if mode == 2:
                return _Binding.missing()
            if mode == 4:
                raise ComfySubgraphNormalizationError(
                    f"Comfy node {sid} ({node_type or 'unknown'}) is bypassed inside a subgraph. "
                    "Neo will not guess bypass rewiring while normalizing subgraphs; un-bypass it or publish a workflow version with explicit stage semantics."
                )
            if node_type.casefold() == "reroute":
                inputs = source.get("inputs") if isinstance(source.get("inputs"), list) else []
                incoming = next((item for item in inputs if isinstance(item, Mapping) and item.get("link") is not None), None)
                if incoming is None:
                    return _Binding.missing()
                link = link_map.get(str(incoming.get("link")))
                if link is None:
                    raise ComfySubgraphNormalizationError(f"Reroute node {sid} references a missing input link.")
                return resolve_source(link["origin_id"], link["origin_slot"])
            if node_type in self.definitions:
                outputs = expand_instance(sid)
                return outputs.get(slot, _Binding.missing())
            return _Binding.link(self._global_node_id(scope_path, sid), slot)

        def positional_instance_values(instance: Mapping[str, Any], definition: Mapping[str, Any]) -> dict[str, Any]:
            named = instance.get("widgets_values_named")
            if isinstance(named, Mapping):
                return {str(key): deepcopy(value) for key, value in named.items()}
            values = instance.get("widgets_values")
            if not isinstance(values, list) or not values:
                return {}
            exposed_inputs = definition.get("inputs") if isinstance(definition.get("inputs"), list) else []
            linked_names = {
                str(item.get("name") or "").strip()
                for item in (instance.get("inputs") if isinstance(instance.get("inputs"), list) else [])
                if isinstance(item, Mapping) and item.get("link") is not None and str(item.get("name") or "").strip()
            }
            result: dict[str, Any] = {}
            value_index = 0
            for raw_input in exposed_inputs:
                if not isinstance(raw_input, Mapping):
                    continue
                if not _serializable_interface_type(raw_input.get("type")):
                    continue
                name = str(raw_input.get("name") or "").strip()
                if not name or name in linked_names:
                    continue
                if value_index >= len(values):
                    break
                result[name] = deepcopy(values[value_index])
                value_index += 1
            return result

        def expand_instance(instance_id: str) -> dict[int, _Binding]:
            if instance_id in instance_cache:
                return instance_cache[instance_id]
            if instance_id in resolving_instances:
                raise ComfySubgraphNormalizationError(f"Subgraph instance cycle detected at node {instance_id}.")
            instance = node_map.get(instance_id)
            if instance is None:
                raise ComfySubgraphNormalizationError(f"Missing subgraph instance node {instance_id}.")
            definition_key = str(instance.get("type") or "").strip()
            definition = self.definitions.get(definition_key)
            if definition is None:
                raise ComfySubgraphNormalizationError(f"Missing subgraph definition {definition_key} for node {instance_id}.")
            mode = instance.get("mode", 0)
            if mode == 2:
                instance_cache[instance_id] = {}
                return {}
            if mode == 4:
                raise ComfySubgraphNormalizationError(
                    f"Subgraph instance {instance_id} is bypassed. Neo will not guess bypass rewiring during subgraph flattening."
                )
            if definition_key in self._definition_stack:
                chain = " -> ".join((*self._definition_stack, definition_key))
                raise ComfySubgraphNormalizationError(f"Recursive Comfy subgraph definitions are not supported: {chain}.")

            resolving_instances.add(instance_id)
            self._definition_stack.append(definition_key)
            self._used_definitions.add(definition_key)
            nested_path = (*scope_path, instance_id)
            self._max_depth = max(self._max_depth, len(nested_path))

            instance_inputs = {
                str(item.get("name") or ""): item
                for item in (instance.get("inputs") if isinstance(instance.get("inputs"), list) else [])
                if isinstance(item, Mapping) and str(item.get("name") or "")
            }
            instance_values = positional_instance_values(instance, definition)
            exposed_inputs = definition.get("inputs") if isinstance(definition.get("inputs"), list) else []
            bindings: dict[int, _Binding] = {}
            for index, raw_input in enumerate(exposed_inputs):
                if not isinstance(raw_input, Mapping):
                    continue
                name = str(raw_input.get("name") or "").strip()
                instance_input = instance_inputs.get(name)
                if instance_input is not None and instance_input.get("link") is not None:
                    link = link_map.get(str(instance_input.get("link")))
                    if link is None:
                        raise ComfySubgraphNormalizationError(
                            f"Subgraph instance {instance_id}.{name} references missing parent link {instance_input.get('link')}."
                        )
                    bindings[index] = resolve_source(link["origin_id"], link["origin_slot"])
                elif name and name in instance_values:
                    bindings[index] = _Binding.literal(instance_values[name])
                else:
                    bindings[index] = _Binding.missing()

            def_name = str(definition.get("name") or definition_key)
            before_nodes = len(self.flat_nodes)
            outputs = self._expand_scope(
                definition,
                scope_path=nested_path,
                parent_bindings=bindings,
                definition_id=definition_key,
                definition_name=def_name,
            )
            after_nodes = len(self.flat_nodes)
            instance_cache[instance_id] = outputs
            self._instance_outputs[nested_path] = outputs
            self.instances.append(
                {
                    "instance_node_id": instance_id,
                    "definition_id": definition_key,
                    "definition_name": def_name,
                    "scope_path": list(nested_path),
                    "depth": len(nested_path),
                    "input_count": len(exposed_inputs),
                    "output_count": len(definition.get("outputs") if isinstance(definition.get("outputs"), list) else []),
                    "flattened_node_count": max(0, after_nodes - before_nodes),
                    "group_titles": [
                        str(item.get("title") or "").strip()
                        for item in (definition.get("groups") if isinstance(definition.get("groups"), list) else [])
                        if isinstance(item, Mapping) and str(item.get("title") or "").strip()
                    ],
                    "reroute_count": len(definition.get("reroutes") if isinstance(definition.get("reroutes"), list) else []),
                }
            )
            self._definition_stack.pop()
            resolving_instances.remove(instance_id)
            return outputs

        # Expand every active subgraph instance, including disconnected utility
        # branches, because Comfy's normal editor format preserves them as graph
        # data even when they are not currently on the terminal path.
        for node_id, node in node_map.items():
            if str(node.get("type") or "").strip() in self.definitions and node.get("mode", 0) != 2:
                expand_instance(node_id)

        # Rebuild every real node's linked inputs using flattened endpoints.
        for local_id, source_node in node_map.items():
            node_type = str(source_node.get("type") or "").strip()
            if node_type in self.definitions:
                continue
            target = self.flat_node_by_id[self._global_node_id(scope_path, local_id)]
            editor_inputs = target.get("inputs") if isinstance(target.get("inputs"), list) else []
            for target_input in editor_inputs:
                if not isinstance(target_input, dict):
                    continue
                link_id = target_input.get("link")
                if link_id is None:
                    continue
                link = link_map.get(str(link_id))
                if link is None:
                    raise ComfySubgraphNormalizationError(
                        f"Node {local_id}.{target_input.get('name') or '?'} references missing link {link_id}."
                    )
                binding = resolve_source(link["origin_id"], link["origin_slot"])
                field = str(target_input.get("name") or "").strip()
                if binding.kind == "missing":
                    target_input["link"] = None
                    continue
                if binding.kind == "literal":
                    target_input["link"] = None
                    if field:
                        self._set_literal_on_target(target, field, binding.value)
                    continue
                flat_link_id = self._new_link_id(scope_path, self._global_node_id(scope_path, local_id), field or "input")
                target_input["link"] = flat_link_id
                self.flat_links.append(
                    {
                        "id": flat_link_id,
                        "origin_id": binding.node_id,
                        "origin_slot": binding.output_slot,
                        "target_id": self._global_node_id(scope_path, local_id),
                        "target_slot": link["target_slot"],
                        "type": link.get("type"),
                    }
                )

        outputs: dict[int, _Binding] = {}
        if output_node_id:
            for link in link_map.values():
                if link["target_id"] != output_node_id:
                    continue
                try:
                    output_index = int(link["target_slot"])
                except (TypeError, ValueError) as exc:
                    raise ComfySubgraphNormalizationError("Subgraph output link has an invalid target slot.") from exc
                outputs[output_index] = resolve_source(link["origin_id"], link["origin_slot"])
        return outputs


def flatten_editor_subgraphs(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Flatten active normal-Comfy subgraph instances into an editor graph.

    The original source workflow is never mutated. The returned graph retains
    ordinary Comfy editor nodes/links so the existing normal-workflow converter
    can continue to own widget mapping and API-prompt normalization. Hierarchy
    and instance lineage are returned separately for analysis/Builder use.
    """

    if not isinstance(raw, Mapping):
        raise ComfySubgraphNormalizationError("Comfy workflow must be an object.")
    definitions = _collect_definitions(raw)
    if not definitions:
        return deepcopy(dict(raw)), {
            "schema_version": SUBGRAPH_IR_SCHEMA_VERSION,
            "flattened": False,
            "definition_count": 0,
            "used_definition_count": 0,
            "instance_count": 0,
            "max_depth": 0,
            "modern_reroute_count": len(raw.get("reroutes") if isinstance(raw.get("reroutes"), list) else []),
            "instances": [],
        }
    return _EditorSubgraphFlattener(raw, definitions).flatten()
