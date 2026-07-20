from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageChops, ImageOps

from .constants import SAM_REFINEMENT_MODEL_IDS, SAM_SESSION_MODEL_ID
from .native_rembg import _providers_for_mode, _refine_alpha, native_rembg_status


def _pixel_prompts(prompts: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    max_x = max(0, int(width) - 1)
    max_y = max(0, int(height) - 1)
    result: list[dict[str, Any]] = []
    for prompt in prompts:
        prompt_type = str(prompt.get("type") or "point")
        if prompt_type == "point":
            result.append({
                "type": "point",
                "label": 1 if int(prompt.get("label") or 0) else 0,
                "data": [
                    int(round(float(prompt.get("x") or 0.0) * max_x)),
                    int(round(float(prompt.get("y") or 0.0) * max_y)),
                ],
            })
        elif prompt_type == "rectangle":
            result.append({
                "type": "rectangle",
                "label": 1,
                "data": [
                    int(round(float(prompt.get("x1") or 0.0) * max_x)),
                    int(round(float(prompt.get("y1") or 0.0) * max_y)),
                    int(round(float(prompt.get("x2") or 1.0) * max_x)),
                    int(round(float(prompt.get("y2") or 1.0) * max_y)),
                ],
            })
    return result


def _open_mask_bytes(value: Any, size: tuple[int, int]) -> Image.Image:
    if isinstance(value, Image.Image):
        image = value
    elif isinstance(value, bytes):
        image = Image.open(io.BytesIO(value))
    else:
        raise RuntimeError(f"Interactive SAM returned an unsupported mask type: {type(value).__name__}")
    return image.convert("L").resize(size, Image.Resampling.BILINEAR)


def _new_session(factory: Any, model_name: str, providers: list[str] | None, **kwargs: Any) -> Any:
    if providers:
        try:
            return factory(model_name, providers=providers, **kwargs)
        except TypeError as provider_error:
            try:
                return factory(model_name, **kwargs)
            except TypeError:
                if kwargs:
                    raise provider_error
                return factory(model_name)
    return factory(model_name, **kwargs)



def _subject_prompts(settings: dict[str, Any]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    legacy = [item for item in (settings.get("sam_prompts") or []) if isinstance(item, dict)]
    subjects = [item for item in (settings.get("sam_subjects") or []) if isinstance(item, dict) and item.get("selected", True)]
    if legacy and subjects and all(str(item.get("source") or "") == "legacy_prompt" for item in subjects):
        return [({"id": "legacy_subject_1", "label": "Subject 1", "source": "legacy_prompt", "selected": True}, legacy)]
    grouped: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for index, subject in enumerate(subjects, start=1):
        prompts: list[dict[str, Any]] = []
        bbox = subject.get("bbox") if isinstance(subject.get("bbox"), dict) else {}
        if bbox:
            prompts.append({"type": "rectangle", **bbox})
        prompts.extend([{**item, "type": "point", "label": 1} for item in (subject.get("keep_points") or []) if isinstance(item, dict)])
        prompts.extend([{**item, "type": "point", "label": 0} for item in (subject.get("remove_points") or []) if isinstance(item, dict)])
        if prompts:
            grouped.append((subject, prompts))
    if grouped:
        return grouped
    return [({"id": "legacy_subject_1", "label": "Subject 1", "source": "legacy_prompt", "selected": True}, legacy)] if legacy else []


def _union_masks(masks: list[Image.Image], size: tuple[int, int], operation: str = "union") -> Image.Image:
    merged = Image.new("L", size, 0)
    operation = operation if operation in {"union", "intersection", "subtract"} else "union"
    for index, mask in enumerate(masks):
        current = mask.convert("L").resize(size, Image.Resampling.BILINEAR)
        if index == 0:
            merged = current
        elif operation == "intersection":
            merged = ImageChops.multiply(merged, current)
        elif operation == "subtract":
            merged = ImageChops.subtract(merged, current)
        else:
            merged = ImageChops.lighter(merged, current)
    return merged

def run_native_sam_selection(
    source_path: Path,
    *,
    settings: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    status = native_rembg_status()
    if not status.get("available"):
        raise RuntimeError(status.get("reason") or "Interactive SAM requires the optional native rembg runtime.")

    from rembg import new_session, remove  # type: ignore

    grouped_subjects = _subject_prompts(settings)
    if not grouped_subjects:
        raise ValueError("Interactive SAM needs at least one selected subject, Keep point, or selection box.")

    with Image.open(source_path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
    width, height = source.size
    providers = _providers_for_mode(str(settings.get("native_provider") or "AUTO"), list(status.get("providers") or []))

    sam_variant = str(settings.get("sam_model_variant") or "sam_vit_b_01ec64")
    sam_quantized = bool(settings.get("sam_quantized", True))
    sam_session = _new_session(
        new_session,
        SAM_SESSION_MODEL_ID,
        providers,
        sam_model=sam_variant,
        sam_quant=sam_quantized,
    )
    source_bytes = source_path.read_bytes()
    subject_masks: list[Image.Image] = []
    subject_rows: list[dict[str, Any]] = []
    total_prompt_count = 0
    for subject, prompts in grouped_subjects:
        pixel_prompts = _pixel_prompts(prompts, width, height)
        if not pixel_prompts:
            continue
        sam_output = remove(
            source_bytes,
            session=sam_session,
            only_mask=True,
            sam_prompt=pixel_prompts,
            force_return_bytes=True,
        )
        subject_mask = _open_mask_bytes(sam_output, source.size)
        if subject_mask.getbbox() is None:
            continue
        subject_masks.append(subject_mask)
        total_prompt_count += len(pixel_prompts)
        subject_rows.append({
            "id": subject.get("id") or f"subject_{len(subject_rows) + 1}",
            "label": subject.get("label") or f"Subject {len(subject_rows) + 1}",
            "source": subject.get("source") or "manual",
            "prompt_count": len(pixel_prompts),
            "bbox": subject.get("bbox") or {},
        })
    if not subject_masks:
        raise RuntimeError("Interactive SAM produced no usable subject masks. Select a tighter box or add a Keep point inside each subject.")
    sam_operation = str(settings.get("sam_mask_operation") or "union")
    sam_mask = _union_masks(subject_masks, source.size, sam_operation)

    notes = [
        f"Interactive SAM segmented {len(subject_masks)} selected subject(s) independently with {total_prompt_count} prompt mark(s) using {sam_variant}{' quantized' if sam_quantized else ''}.",
        f"Independent subject masks were combined with {sam_operation} before edge refinement, so unselected people remain transparent.",
    ]
    refine_mode = str(settings.get("sam_refine_mode") or "birefnet_gate")
    refinement_model = str(settings.get("sam_refine_model") or "birefnet-general")
    refinement_used = False
    refinement_fallback = False

    if refine_mode == "birefnet_gate":
        if refinement_model not in SAM_REFINEMENT_MODEL_IDS:
            raise ValueError(f"Unsupported Interactive SAM refinement model: {refinement_model}")
        try:
            refinement_session = _new_session(new_session, refinement_model, providers)
            refinement_output = remove(
                source_bytes,
                session=refinement_session,
                only_mask=True,
                force_return_bytes=True,
            )
            refinement_mask = _open_mask_bytes(refinement_output, source.size)
            gate = _refine_alpha(
                sam_mask,
                threshold=0.0,
                expand=int(settings.get("sam_gate_expand") or 0),
                feather=int(settings.get("sam_gate_feather") or 0),
            )
            final_alpha = ImageChops.multiply(refinement_mask, gate)
            if final_alpha.getbbox() is None:
                raise RuntimeError("BiRefNet-gated mask was empty.")
            refinement_used = True
            notes.append(f"BiRefNet edge refinement used {refinement_model} inside the SAM-selected region.")
        except Exception as exc:
            if not settings.get("sam_refine_fallback", True):
                raise
            final_alpha = sam_mask
            refinement_fallback = True
            notes.append(f"BiRefNet refinement was unavailable; the SAM mask was kept: {exc}")
    else:
        final_alpha = sam_mask
        notes.append("SAM-only mode kept the prompted segmentation mask without a second model pass.")

    final_alpha = _refine_alpha(
        final_alpha,
        threshold=float(settings.get("mask_threshold") or 0.0),
        expand=int(settings.get("mask_expand") or 0),
        feather=int(settings.get("mask_feather") or 0),
    )
    if final_alpha.getbbox() is None:
        raise RuntimeError("Interactive SAM produced an empty foreground mask. Add a Keep point inside the subject or draw a tighter box.")

    foreground = source.copy()
    foreground.putalpha(final_alpha)
    output_root.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex[:12]
    foreground_path = output_root / f"NeoStudioBackgroundRemovedSAM_{token}.png"
    mask_path = output_root / f"NeoStudioBackgroundMaskSAM_{token}.png"
    foreground.save(foreground_path, format="PNG")
    if settings.get("save_mask", True):
        final_alpha.save(mask_path, format="PNG")

    all_subject_prompts = [prompt for _subject, prompts in grouped_subjects for prompt in prompts]
    point_count = sum(1 for item in all_subject_prompts if item.get("type") == "point")
    box_count = sum(1 for item in all_subject_prompts if item.get("type") == "rectangle")
    output_metadata = {
        "background_removal_role": "foreground",
        "engine": "native_sam",
        "sam_model_variant": sam_variant,
        "sam_quantized": sam_quantized,
        "sam_prompt_count": total_prompt_count,
        "sam_subject_count": len(subject_rows),
        "sam_subject_ids": [item.get("id") for item in subject_rows],
        "sam_keep_points": sum(1 for item in all_subject_prompts if item.get("type") == "point" and int(item.get("label") or 0) == 1),
        "sam_remove_points": sum(1 for item in all_subject_prompts if item.get("type") == "point" and int(item.get("label") or 0) == 0),
        "sam_boxes": box_count,
        "sam_execution": "native_onnx",
        "sam_refinement_mode": refine_mode,
        "sam_refinement_model": refinement_model if refinement_used else "",
        "sam_refinement_fallback": refinement_fallback,
    }
    outputs = [{
        "kind": "image",
        "filename": foreground_path.name,
        "path": str(foreground_path),
        "role": "foreground",
        "metadata": dict(output_metadata),
    }]
    if settings.get("save_mask", True):
        outputs.append({
            "kind": "image",
            "filename": mask_path.name,
            "path": str(mask_path),
            "role": "mask",
            "metadata": {
                **output_metadata,
                "background_removal_role": "mask",
            },
        })
    notes.append(f"Prompt summary: {point_count} point(s), {box_count} selection box(es).")
    return {
        "outputs": outputs,
        "engine": "native_sam",
        "model": sam_variant,
        "runtime": status,
        "notes": notes,
        "selection": {
            "prompt_count": total_prompt_count,
            "subject_count": len(subject_rows),
            "subjects": subject_rows,
            "point_count": point_count,
            "box_count": box_count,
            "refinement_used": refinement_used,
            "refinement_fallback": refinement_fallback,
        },
    }
