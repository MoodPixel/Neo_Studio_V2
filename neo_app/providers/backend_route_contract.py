from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from neo_app.models.route_matrix import (
    RouteMatrixEntry,
    available_modes_for_route,
    list_model_backend_routes,
    normalize_backend,
    normalize_mode,
    resolve_model_backend_route,
)


BACKEND_ROUTE_CONTRACT_VERSION = "0.1.0"


@dataclass(frozen=True)
class BackendRouteContract:
    """Backend plug-in route contract for a single family/loader/mode tuple.

    This is the provider-facing companion to the model route matrix. It keeps
    route availability backend-specific so Comfy routes do not accidentally make
    Forge/A1111 routes selectable or compilable.
    """

    backend: str
    family: str
    loader: str
    mode: str
    state: str
    selectable: bool
    reason: str = ""
    workflow_type: str | None = None
    compiler_id: str | None = None
    required_assets: list[str] = field(default_factory=list)
    provider_nodes: dict[str, str] = field(default_factory=dict)
    parameter_profile: str | None = None

    @property
    def can_compile(self) -> bool:
        return self.state in {"available", "experimental_available"} and bool(self.compiler_id)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"can_compile": self.can_compile}


def _from_route(route: RouteMatrixEntry) -> BackendRouteContract:
    return BackendRouteContract(
        backend=route.backend,
        family=route.family,
        loader=route.loader,
        mode=route.mode,
        state=route.state,
        selectable=route.selectable,
        reason=route.reason,
        workflow_type=route.workflow_type,
        compiler_id=route.compiler_id,
        required_assets=list(route.requires),
        provider_nodes=dict(route.provider_nodes),
        parameter_profile=route.parameter_profile,
    )


def get_backend_route_contract(
    backend: str,
    family: str,
    loader: str,
    mode: str,
) -> BackendRouteContract:
    """Resolve the exact route contract a backend adapter must obey."""

    route = resolve_model_backend_route(family, loader, normalize_mode(mode), normalize_backend(backend))
    return _from_route(route)


def list_backend_route_contracts(backend: str | None = None) -> list[dict[str, Any]]:
    """Return route contracts for one backend or all declared base backends."""

    return [_from_route(resolve_model_backend_route(row["family"], row["loader"], row["mode"], row["backend"])).as_dict() for row in list_model_backend_routes(backend)]


def selectable_modes_for_backend(
    backend: str,
    family: str,
    loader: str,
    *,
    include_experimental: bool = True,
) -> list[str]:
    """Backend-aware helper used by future provider adapters and UI payloads."""

    return available_modes_for_route(family, loader, backend, include_experimental=include_experimental)


def backend_route_contract_payload(backend: str | None = None) -> dict[str, Any]:
    return {
        "version": BACKEND_ROUTE_CONTRACT_VERSION,
        "contract": "backend + family + loader + mode route contracts; provider adapters must not inherit routes from another backend",
        "routes": list_backend_route_contracts(backend),
    }
