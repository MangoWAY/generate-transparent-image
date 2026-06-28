#!/usr/bin/env python3
"""Recover straight RGBA from paired or material-aware 2x2 source masters."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
except ImportError as exc:  # pragma: no cover - exercised by environment setup
    raise SystemExit(
        "recover_alpha.py requires Pillow and NumPy. Use a Python environment "
        "that provides both packages."
    ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recover straight RGBA from either a black-left/white-right pair or "
            "a 2x2 black/white/opaque-core/soft-effect material master."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--layout",
        choices=("paired", "material-2x2"),
        default="paired",
        help="Input layout (default: paired).",
    )
    parser.add_argument("--alpha-out", type=Path)
    parser.add_argument("--soft-alpha-out", type=Path)
    parser.add_argument("--core-mask-out", type=Path)
    parser.add_argument("--soft-mask-out", type=Path)
    parser.add_argument("--preview-out", type=Path)
    parser.add_argument("--preview-grid-out", type=Path)
    parser.add_argument("--soft-preview-grid-out", type=Path)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument(
        "--aspect",
        help="Final canvas ratio such as 1:1, 4:5, 16:9, or a decimal.",
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=0.04,
        help="Transparent padding around detected content as a fraction (default: 0.04).",
    )
    parser.add_argument(
        "--max-shift",
        type=float,
        default=0.04,
        help="Maximum translation search as a fraction of the shorter panel edge.",
    )
    parser.add_argument(
        "--no-align", action="store_true", help="Disable automatic translation alignment."
    )
    parser.add_argument(
        "--core-erode",
        type=int,
        default=2,
        help="Pixels removed from the opaque-core mask edge before forcing alpha (default: 2).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with status 2 when diagnostics grade the recovery as fail.",
    )
    return parser.parse_args()


def ensure_parent(path: Path | None) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


def border_pixels(image: np.ndarray, fraction: float = 0.035) -> np.ndarray:
    h, w, _ = image.shape
    band = max(2, int(round(min(h, w) * fraction)))
    return np.concatenate(
        [
            image[:band].reshape(-1, 3),
            image[-band:].reshape(-1, 3),
            image[:, :band].reshape(-1, 3),
            image[:, -band:].reshape(-1, 3),
        ],
        axis=0,
    )


def robust_backdrop(image: np.ndarray, expected: str) -> np.ndarray:
    pixels = border_pixels(image)
    luminance = pixels.mean(axis=1)
    if expected == "black":
        selected = pixels[luminance <= np.quantile(luminance, 0.35)]
    else:
        selected = pixels[luminance >= np.quantile(luminance, 0.65)]
    if len(selected) < 32:
        selected = pixels
    return np.median(selected, axis=0).astype(np.float32)


def shifted(image: np.ndarray, dx: int, dy: int, fill: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Move source by (dx, dy) into a same-sized canvas and return validity mask."""
    h, w, _ = image.shape
    output = np.empty_like(image)
    output[...] = fill
    valid = np.zeros((h, w), dtype=bool)

    src_x0 = max(0, -dx)
    src_x1 = min(w, w - dx)
    src_y0 = max(0, -dy)
    src_y1 = min(h, h - dy)
    dst_x0 = max(0, dx)
    dst_x1 = min(w, w + dx)
    dst_y0 = max(0, dy)
    dst_y1 = min(h, h + dy)

    if src_x1 > src_x0 and src_y1 > src_y0:
        output[dst_y0:dst_y1, dst_x0:dst_x1] = image[src_y0:src_y1, src_x0:src_x1]
        valid[dst_y0:dst_y1, dst_x0:dst_x1] = True
    return output, valid


def shifted_2d(image: np.ndarray, dx: int, dy: int, fill: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    shifted_image, valid = shifted(image[..., None], dx, dy, np.array([fill], dtype=image.dtype))
    return shifted_image[..., 0], valid


def resize_array(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    data = np.clip(np.round(image * 255.0), 0, 255).astype(np.uint8)
    return np.asarray(Image.fromarray(data).resize(size, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0


def alignment_cost(
    left: np.ndarray,
    right: np.ndarray,
    valid: np.ndarray,
    black: np.ndarray,
    white: np.ndarray,
) -> float:
    baseline = white - black
    baseline_norm = float(np.dot(baseline, baseline)) + 1e-8
    delta = right - left
    t = np.sum(delta * baseline, axis=2) / baseline_norm
    residual = delta - t[..., None] * baseline
    residual_norm = np.sqrt(np.mean(residual * residual, axis=2))

    left_activity = np.max(np.abs(left - black), axis=2)
    right_activity = np.max(np.abs(right - white), axis=2)
    active = valid & ((left_activity > 0.025) | (right_activity > 0.025))
    if int(active.sum()) < 64:
        return 1e6

    invalid_t = np.maximum(-t, 0.0) + np.maximum(t - 1.0, 0.0)
    # Median protects the registration search from a few real translucent outliers.
    return float(
        np.median(residual_norm[active])
        + 0.35 * np.mean(np.minimum(invalid_t[active], 1.0))
    )


def find_translation(
    left: np.ndarray,
    right: np.ndarray,
    black: np.ndarray,
    white: np.ndarray,
    max_shift_fraction: float,
) -> tuple[int, int, float]:
    h, w, _ = left.shape
    scale = min(1.0, 256.0 / max(h, w))
    small_size = (max(24, int(round(w * scale))), max(24, int(round(h * scale))))
    small_left = resize_array(left, small_size)
    small_right = resize_array(right, small_size)
    radius = max(1, int(round(min(small_size) * max_shift_fraction)))
    radius = min(radius, 14)

    best = (0, 0, math.inf)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            candidate, valid = shifted(small_right, dx, dy, white)
            cost = alignment_cost(small_left, candidate, valid, black, white)
            if cost < best[2]:
                best = (dx, dy, cost)

    coarse_dx = int(round(best[0] / scale))
    coarse_dy = int(round(best[1] / scale))
    full_radius = max(1, int(math.ceil(1.5 / max(scale, 1e-6))))
    refined = (coarse_dx, coarse_dy, math.inf)
    for dy in range(coarse_dy - full_radius, coarse_dy + full_radius + 1):
        for dx in range(coarse_dx - full_radius, coarse_dx + full_radius + 1):
            candidate, valid = shifted(right, dx, dy, white)
            cost = alignment_cost(left, candidate, valid, black, white)
            if cost < refined[2]:
                refined = (dx, dy, cost)
    return refined


def decode_semantic_mask(panel: np.ndarray) -> tuple[np.ndarray, float]:
    """Normalize a generated white-on-black semantic mask panel."""
    luminance = panel.mean(axis=2)
    border = border_pixels(panel).mean(axis=1)
    background = float(np.median(border))
    foreground = float(np.quantile(luminance, 0.995))
    contrast = foreground - background
    if contrast < 0.20:
        raise ValueError("semantic mask panel lacks a clear white-on-black signal")
    mask = np.clip((luminance - background) / max(contrast, 1e-6), 0.0, 1.0)
    return mask.astype(np.float32), contrast


def mask_alignment_cost(target: np.ndarray, candidate: np.ndarray, valid: np.ndarray) -> float:
    target_binary = target > 0.04
    candidate_binary = candidate > 0.35
    candidate_binary &= valid
    target_count = int(target_binary.sum())
    candidate_count = int(candidate_binary.sum())
    if target_count < 16 or candidate_count < 16:
        return 1e6
    intersection = int((target_binary & candidate_binary).sum())
    dice = (2.0 * intersection) / max(target_count + candidate_count, 1)
    outside = int((candidate_binary & ~target_binary).sum()) / max(candidate_count, 1)
    return float(1.0 - dice + 0.25 * outside)


def find_mask_translation(
    target_alpha: np.ndarray,
    opaque_core: np.ndarray,
    soft_effect: np.ndarray,
    max_shift_fraction: float,
) -> tuple[int, int, float]:
    """Register the two semantic masks together to the recovered subject support."""
    combined = np.maximum(opaque_core, soft_effect)
    h, w = target_alpha.shape
    scale = min(1.0, 256.0 / max(h, w))
    small_size = (max(24, int(round(w * scale))), max(24, int(round(h * scale))))
    small_target = resize_array(target_alpha[..., None].repeat(3, axis=2), small_size)[..., 0]
    small_combined = resize_array(combined[..., None].repeat(3, axis=2), small_size)[..., 0]
    radius = max(1, int(round(min(small_size) * max_shift_fraction)))
    radius = min(radius, 14)

    best = (0, 0, math.inf)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            candidate, valid = shifted_2d(small_combined, dx, dy)
            cost = mask_alignment_cost(small_target, candidate, valid)
            if cost < best[2]:
                best = (dx, dy, cost)

    coarse_dx = int(round(best[0] / scale))
    coarse_dy = int(round(best[1] / scale))
    full_radius = max(1, int(math.ceil(1.5 / max(scale, 1e-6))))
    refined = (coarse_dx, coarse_dy, math.inf)
    for dy in range(coarse_dy - full_radius, coarse_dy + full_radius + 1):
        for dx in range(coarse_dx - full_radius, coarse_dx + full_radius + 1):
            candidate, valid = shifted_2d(combined, dx, dy)
            cost = mask_alignment_cost(target_alpha, candidate, valid)
            if cost < refined[2]:
                refined = (dx, dy, cost)
    return refined


def erode_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    binary = (mask >= 0.5).astype(np.uint8) * 255
    if radius > 0:
        size = radius * 2 + 1
        binary = np.asarray(
            Image.fromarray(binary, "L").filter(ImageFilter.MinFilter(size=size)),
            dtype=np.uint8,
        )
    return (binary.astype(np.float32) / 255.0).astype(np.float32)


def apply_material_masks(
    rgba: np.ndarray,
    left: np.ndarray,
    aligned_right: np.ndarray,
    opaque_core: np.ndarray,
    soft_effect: np.ndarray,
    core_erode: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, float]]:
    """Force semantic solid interiors opaque while preserving pair-recovered soft edges."""
    output = rgba.copy()
    alpha_before = output[..., 3].copy()
    core_candidate = erode_mask(opaque_core, max(0, core_erode))
    soft_clean = np.clip(soft_effect, 0.0, 1.0).astype(np.float32)

    # Soft semantics win conflicts. Generated masks often mark glow, ink, hair
    # fringe, or highlights in both panels; forcing those overlaps opaque would
    # destroy the very translucency the 2x2 layout is meant to preserve.
    overlap = (core_candidate >= 0.5) & (soft_clean >= 0.5)
    core_safe = core_candidate.copy()
    core_safe[soft_clean >= 0.5] = 0.0

    core_pixels = core_safe >= 0.5
    soft_support = alpha_before > 0.02
    keep_mask = np.maximum(core_safe, soft_clean) > 0.20
    core_count = int(core_pixels.sum())
    support_count = int(soft_support.sum())

    if core_count:
        core_rgb = np.clip((left + aligned_right) * 0.5, 0.0, 1.0)
        output[..., :3][core_pixels] = core_rgb[core_pixels]
        output[..., 3][core_pixels] = 1.0

    if core_count:
        core_before = alpha_before[core_pixels]
        core_after = output[..., 3][core_pixels]
        low_before = float((core_before < 0.98).sum() / core_count)
        outside_support = float((~soft_support[core_pixels]).sum() / core_count)
        p05_before = float(np.quantile(core_before, 0.05))
        p05_after = float(np.quantile(core_after, 0.05))
    else:
        low_before = 0.0
        outside_support = 0.0
        p05_before = 1.0
        p05_after = 1.0

    support_recall = (
        float((soft_support & keep_mask).sum() / support_count) if support_count else 1.0
    )
    union = (core_candidate >= 0.5) | (soft_clean >= 0.5)
    metrics = {
        "core_mask_coverage": float(core_count / max(core_safe.size, 1)),
        "soft_mask_coverage": float((soft_clean >= 0.5).sum() / max(soft_clean.size, 1)),
        "core_low_alpha_fraction_before": low_before,
        "core_low_alpha_fraction_after": float(
            (output[..., 3][core_pixels] < 0.98).sum() / core_count
        )
        if core_count
        else 0.0,
        "core_alpha_p05_before": p05_before,
        "core_alpha_p05_after": p05_after,
        "core_outside_pair_support_fraction": outside_support,
        "semantic_support_recall": support_recall,
        "core_soft_overlap_fraction": float(overlap.sum() / max(union.sum(), 1)),
    }
    return output, core_safe, soft_clean, metrics


def solve_rgba(
    left: np.ndarray,
    right: np.ndarray,
    valid: np.ndarray,
    black: np.ndarray,
    white: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    baseline = white - black
    baseline_norm = float(np.dot(baseline, baseline)) + 1e-8
    delta = right - left
    transmission_raw = np.sum(delta * baseline, axis=2) / baseline_norm
    transmission = np.clip(transmission_raw, 0.0, 1.0)
    alpha = 1.0 - transmission

    residual = delta - transmission[..., None] * baseline
    residual_norm = np.sqrt(np.mean(residual * residual, axis=2))

    safe_alpha = np.maximum(alpha, 0.025)[..., None]
    foreground_left = (left - transmission[..., None] * black) / safe_alpha
    foreground_right = (right - transmission[..., None] * white) / safe_alpha
    disagreement = np.sqrt(np.mean((foreground_left - foreground_right) ** 2, axis=2))
    foreground = np.clip((foreground_left + foreground_right) * 0.5, 0.0, 1.0)

    alpha[~valid] = 0.0
    foreground[~valid] = 0.0
    foreground[alpha < 0.004] = 0.0
    alpha[alpha < 0.002] = 0.0

    # Paired generations occasionally add a one-pixel canvas/seam highlight at
    # the outer panel edge. Prompts require generous subject padding, so panel
    # border pixels are guaranteed background and can be cleared safely. This
    # prevents a thin vertical line from expanding the final content crop.
    border_guard = max(1, int(round(min(alpha.shape) * 0.003)))
    alpha[:border_guard] = 0.0
    alpha[-border_guard:] = 0.0
    alpha[:, :border_guard] = 0.0
    alpha[:, -border_guard:] = 0.0
    foreground[:border_guard] = 0.0
    foreground[-border_guard:] = 0.0
    foreground[:, :border_guard] = 0.0
    foreground[:, -border_guard:] = 0.0

    active = valid & (alpha > 0.02)
    opaque_enough = valid & (alpha > 0.10)
    if int(active.sum()) < 16:
        active = valid
    if int(opaque_enough.sum()) < 16:
        opaque_enough = active

    invalid = valid & ((transmission_raw < -0.02) | (transmission_raw > 1.02))
    metrics = {
        "residual_p50": float(np.quantile(residual_norm[active], 0.50)),
        "residual_p95": float(np.quantile(residual_norm[active], 0.95)),
        "foreground_disagreement_p90": float(
            np.quantile(np.minimum(disagreement[opaque_enough], 2.0), 0.90)
        ),
        "invalid_transmission_fraction": float(invalid.sum() / max(valid.sum(), 1)),
        "foreground_coverage": float((valid & (alpha > 0.02)).sum() / max(valid.sum(), 1)),
    }
    rgba = np.dstack([foreground, alpha]).astype(np.float32)
    return rgba, residual_norm, metrics


def parse_aspect(value: str | None) -> float | None:
    if value is None or value.lower() in {"source", "auto"}:
        return None
    if ":" in value:
        left, right = value.split(":", 1)
        ratio = float(left) / float(right)
    elif "/" in value:
        left, right = value.split("/", 1)
        ratio = float(left) / float(right)
    else:
        ratio = float(value)
    if not math.isfinite(ratio) or ratio <= 0 or ratio > 20:
        raise ValueError("aspect ratio must be positive and no greater than 20:1")
    return ratio


def content_bbox(alpha: np.ndarray, threshold: float = 0.005) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(alpha > threshold)
    h, w = alpha.shape
    if len(xs) == 0:
        return 0, 0, w, h
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def final_canvas_bounds(
    alpha: np.ndarray, aspect: float | None, padding: float
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = content_bbox(alpha)
    content_w = max(1, x1 - x0)
    content_h = max(1, y1 - y0)
    pad_x = int(round(content_w * max(0.0, padding)))
    pad_y = int(round(content_h * max(0.0, padding)))
    x0 -= pad_x
    x1 += pad_x
    y0 -= pad_y
    y1 += pad_y

    crop_w = x1 - x0
    crop_h = y1 - y0
    if aspect is not None:
        current = crop_w / max(crop_h, 1)
        if current < aspect:
            target_w = int(math.ceil(crop_h * aspect))
            extra = target_w - crop_w
            x0 -= extra // 2
            x1 += extra - extra // 2
        else:
            target_h = int(math.ceil(crop_w / aspect))
            extra = target_h - crop_h
            y0 -= extra // 2
            y1 += extra - extra // 2

    return x0, y0, x1, y1


def fit_to_bounds(image: np.ndarray, bounds: tuple[int, int, int, int]) -> np.ndarray:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = bounds
    out_w = max(1, x1 - x0)
    out_h = max(1, y1 - y0)
    output_shape = (out_h, out_w) + image.shape[2:]
    output = np.zeros(output_shape, dtype=image.dtype)

    src_x0 = max(0, x0)
    src_y0 = max(0, y0)
    src_x1 = min(w, x1)
    src_y1 = min(h, y1)
    dst_x0 = src_x0 - x0
    dst_y0 = src_y0 - y0
    output[
        dst_y0 : dst_y0 + (src_y1 - src_y0),
        dst_x0 : dst_x0 + (src_x1 - src_x0),
    ] = image[src_y0:src_y1, src_x0:src_x1]
    return output


def fit_final_canvas(rgba: np.ndarray, aspect: float | None, padding: float) -> np.ndarray:
    bounds = final_canvas_bounds(rgba[..., 3], aspect, padding)
    return fit_to_bounds(rgba, bounds)


def to_u8(image: np.ndarray) -> np.ndarray:
    return np.clip(np.round(image * 255.0), 0, 255).astype(np.uint8)


def save_preview(rgba: np.ndarray, path: Path) -> None:
    h, w, _ = rgba.shape
    cell = max(8, min(24, min(h, w) // 16 or 8))
    yy, xx = np.indices((h, w))
    checker = ((xx // cell + yy // cell) % 2).astype(np.float32)
    background = (0.72 + checker[..., None] * 0.16).repeat(3, axis=2)
    rgb = rgba[..., :3]
    alpha = rgba[..., 3:4]
    preview = rgb * alpha + background * (1.0 - alpha)
    Image.fromarray(to_u8(preview), "RGB").save(path)


def composite_over(rgba: np.ndarray, background: np.ndarray) -> np.ndarray:
    rgb = rgba[..., :3]
    alpha = rgba[..., 3:4]
    return rgb * alpha + background * (1.0 - alpha)


def save_preview_grid(rgba: np.ndarray, path: Path) -> None:
    """Save adversarial previews that expose leaks hidden by checkerboards."""
    h, w, _ = rgba.shape
    scale = min(1.0, 420.0 / max(h, w))
    tile_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    cell = max(8, min(24, min(h, w) // 16 or 8))
    yy, xx = np.indices((h, w))
    checker_value = (0.72 + ((xx // cell + yy // cell) % 2)[..., None] * 0.16)
    backgrounds = [
        ("checker", checker_value.repeat(3, axis=2)),
        ("black", np.zeros((h, w, 3), dtype=np.float32)),
        ("white", np.ones((h, w, 3), dtype=np.float32)),
        ("green", np.broadcast_to(np.array([0.0, 1.0, 0.35]), (h, w, 3))),
        ("magenta", np.broadcast_to(np.array([1.0, 0.0, 1.0]), (h, w, 3))),
    ]

    label_height = 24
    grid = Image.new("RGB", (tile_size[0] * 3, (tile_size[1] + label_height) * 2), (40, 40, 40))
    draw = ImageDraw.Draw(grid)
    for index, (label, background) in enumerate(backgrounds):
        rendered = Image.fromarray(to_u8(composite_over(rgba, background)), "RGB")
        rendered = rendered.resize(tile_size, Image.Resampling.LANCZOS)
        column = index % 3
        row = index // 3
        x = column * tile_size[0]
        y = row * (tile_size[1] + label_height)
        grid.paste(rendered, (x, y + label_height))
        draw.text((x + 6, y + 5), label, fill=(245, 245, 245))

    alpha_preview = Image.fromarray(to_u8(rgba[..., 3]), "L").convert("RGB")
    alpha_preview = alpha_preview.resize(tile_size, Image.Resampling.LANCZOS)
    x = 2 * tile_size[0]
    y = tile_size[1] + label_height
    grid.paste(alpha_preview, (x, y + label_height))
    draw.text((x + 6, y + 5), "alpha", fill=(245, 245, 245))
    grid.save(path)


def grade_report(metrics: dict[str, float], registration_score: float) -> tuple[str, list[str]]:
    """Grade for practical asset use, not pixel-perfect source reconstruction.

    The paired render comes from a generative model, so small edge, glow, and
    translucency differences are expected.  Failures are reserved for values
    that usually produce visible double edges, color contamination, or broken
    alpha at normal game-asset display sizes.
    """
    failures: list[str] = []
    warnings: list[str] = []
    if registration_score > 0.10:
        failures.append("paired subjects are visibly inconsistent after registration")
    elif registration_score > 0.055:
        warnings.append("paired-subject registration may soften small details")

    if metrics["residual_p95"] > 0.25:
        failures.append("strong background-dependent color contamination is likely visible")
    elif metrics["residual_p95"] > 0.14:
        warnings.append("minor background-dependent differences may affect fine glow details")

    if metrics["foreground_disagreement_p90"] > 0.48:
        failures.append("black/white foreground colors disagree visibly")
    elif metrics["foreground_disagreement_p90"] > 0.30:
        warnings.append("black/white foreground colors differ in some fine details")

    if metrics["invalid_transmission_fraction"] > 0.12:
        failures.append("large regions violate the two-background compositing model")
    elif metrics["invalid_transmission_fraction"] > 0.06:
        warnings.append("some recovered pixels may show edge or glow artifacts")

    coverage = metrics["foreground_coverage"]
    if coverage < 0.001:
        failures.append("no usable foreground was detected")
    elif coverage > 0.82:
        warnings.append("foreground nearly fills the panel; inspect for clipping")

    if "semantic_support_recall" in metrics:
        if metrics["semantic_support_recall"] < 0.45:
            failures.append("semantic masks miss most of the recovered subject")
        elif metrics["semantic_support_recall"] < 0.75:
            warnings.append("semantic masks omit some recovered subject detail")

        outside = metrics["core_outside_pair_support_fraction"]
        if outside > 0.30:
            failures.append("opaque-core mask extends far outside paired subject support")
        elif outside > 0.12:
            warnings.append("opaque-core mask may be slightly misregistered")

        if metrics["core_soft_overlap_fraction"] > 0.25:
            warnings.append("opaque-core and soft-effect masks overlap substantially")

    if failures:
        return "fail", failures + warnings
    if warnings:
        return "warn", warnings
    return "pass", []


def main() -> int:
    args = parse_args()
    for path in [
        args.output,
        args.alpha_out,
        args.soft_alpha_out,
        args.core_mask_out,
        args.soft_mask_out,
        args.preview_out,
        args.preview_grid_out,
        args.soft_preview_grid_out,
        args.report_out,
    ]:
        ensure_parent(path)

    source = Image.open(args.input).convert("RGB")
    width, height = source.size
    if width < 64 or height < 32:
        raise SystemExit("source image is too small for recovery")
    if width % 2:
        # A one-pixel model/export discrepancy is safer to crop than to resize.
        source = source.crop((0, 0, width - 1, height))
        width -= 1
    if args.layout == "material-2x2" and height % 2:
        source = source.crop((0, 0, width, height - 1))
        height -= 1

    panel_width = width // 2
    panel_height = height if args.layout == "paired" else height // 2
    array = np.asarray(source, dtype=np.float32) / 255.0
    left = array[:panel_height, :panel_width]
    right = array[:panel_height, panel_width:]
    core_panel = soft_panel = None
    if args.layout == "material-2x2":
        core_panel = array[panel_height:, :panel_width]
        soft_panel = array[panel_height:, panel_width:]

    black = robust_backdrop(left, "black")
    white = robust_backdrop(right, "white")
    contrast = float(np.linalg.norm(white - black))
    if contrast < 1.1:
        raise SystemExit(
            "estimated panel backdrops are not sufficiently black and white; "
            "regenerate the paired source"
        )

    if args.no_align:
        dx = dy = 0
        registration_score = alignment_cost(
            left, right, np.ones(left.shape[:2], dtype=bool), black, white
        )
    else:
        dx, dy, registration_score = find_translation(
            left, right, black, white, max(0.0, args.max_shift)
        )

    aligned_right, valid = shifted(right, dx, dy, white)
    soft_rgba, _residual, metrics = solve_rgba(left, aligned_right, valid, black, white)
    rgba = soft_rgba.copy()
    core_mask = soft_mask = None
    core_contrast = soft_contrast = None
    mask_dx = mask_dy = 0
    mask_registration_score = None

    if args.layout == "material-2x2":
        assert core_panel is not None and soft_panel is not None
        raw_core, core_contrast = decode_semantic_mask(core_panel)
        raw_soft, soft_contrast = decode_semantic_mask(soft_panel)
        if args.no_align:
            combined = np.maximum(raw_core, raw_soft)
            mask_registration_score = mask_alignment_cost(
                soft_rgba[..., 3], combined, np.ones(combined.shape, dtype=bool)
            )
        else:
            mask_dx, mask_dy, mask_registration_score = find_mask_translation(
                soft_rgba[..., 3], raw_core, raw_soft, max(0.0, args.max_shift)
            )
        core_mask, _core_valid = shifted_2d(raw_core, mask_dx, mask_dy)
        soft_mask, _soft_valid = shifted_2d(raw_soft, mask_dx, mask_dy)
        rgba, core_mask, soft_mask, material_metrics = apply_material_masks(
            soft_rgba,
            left,
            aligned_right,
            core_mask,
            soft_mask,
            max(0, args.core_erode),
        )
        metrics.update(material_metrics)

    aspect = parse_aspect(args.aspect)
    bounds = final_canvas_bounds(rgba[..., 3], aspect, args.padding)
    final_rgba = fit_to_bounds(rgba, bounds)
    final_soft_rgba = fit_to_bounds(soft_rgba, bounds)
    final_soft_alpha = fit_to_bounds(soft_rgba[..., 3], bounds)
    final_core_mask = fit_to_bounds(core_mask, bounds) if core_mask is not None else None
    final_soft_mask = fit_to_bounds(soft_mask, bounds) if soft_mask is not None else None

    Image.fromarray(to_u8(final_rgba), "RGBA").save(args.output)
    if args.alpha_out:
        Image.fromarray(to_u8(final_rgba[..., 3]), "L").save(args.alpha_out)
    if args.soft_alpha_out:
        Image.fromarray(to_u8(final_soft_alpha), "L").save(args.soft_alpha_out)
    if args.core_mask_out and final_core_mask is not None:
        Image.fromarray(to_u8(final_core_mask), "L").save(args.core_mask_out)
    if args.soft_mask_out and final_soft_mask is not None:
        Image.fromarray(to_u8(final_soft_mask), "L").save(args.soft_mask_out)
    if args.preview_out:
        save_preview(final_rgba, args.preview_out)
    if args.preview_grid_out:
        save_preview_grid(final_rgba, args.preview_grid_out)
    if args.soft_preview_grid_out:
        save_preview_grid(final_soft_rgba, args.soft_preview_grid_out)

    grade, issues = grade_report(metrics, registration_score)
    report = {
        "input": str(args.input),
        "output": str(args.output),
        "layout": args.layout,
        "source_size": [width, height],
        "panel_size": [panel_width, panel_height],
        "final_size": [int(final_rgba.shape[1]), int(final_rgba.shape[0])],
        "requested_aspect": args.aspect,
        "estimated_black_rgb": [round(float(x), 6) for x in black],
        "estimated_white_rgb": [round(float(x), 6) for x in white],
        "backdrop_contrast": round(contrast, 6),
        "alignment_translation": {"x": dx, "y": dy},
        "registration_score": round(float(registration_score), 6),
        "semantic_mask_alignment_translation": {"x": mask_dx, "y": mask_dy}
        if args.layout == "material-2x2"
        else None,
        "semantic_mask_registration_score": round(float(mask_registration_score), 6)
        if mask_registration_score is not None
        else None,
        "opaque_core_mask_contrast": round(float(core_contrast), 6)
        if core_contrast is not None
        else None,
        "soft_effect_mask_contrast": round(float(soft_contrast), 6)
        if soft_contrast is not None
        else None,
        **{key: round(float(value), 6) for key, value in metrics.items()},
        "grade": grade,
        "issues": issues,
    }
    if args.report_out:
        args.report_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 2 if args.strict and grade == "fail" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
