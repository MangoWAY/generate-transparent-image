#!/usr/bin/env python3
"""Recover a straight-alpha PNG from one horizontal black/white source pair."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # pragma: no cover - exercised by environment setup
    raise SystemExit(
        "recover_alpha.py requires Pillow and NumPy. Use a Python environment "
        "that provides both packages."
    ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split an exact 50/50 black-left/white-right source, align the right "
            "copy, solve straight RGBA, and emit diagnostics."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alpha-out", type=Path)
    parser.add_argument("--preview-out", type=Path)
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


def fit_final_canvas(rgba: np.ndarray, aspect: float | None, padding: float) -> np.ndarray:
    h, w, _ = rgba.shape
    x0, y0, x1, y1 = content_bbox(rgba[..., 3])
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

    out_w = max(1, x1 - x0)
    out_h = max(1, y1 - y0)
    output = np.zeros((out_h, out_w, 4), dtype=np.float32)

    src_x0 = max(0, x0)
    src_y0 = max(0, y0)
    src_x1 = min(w, x1)
    src_y1 = min(h, y1)
    dst_x0 = src_x0 - x0
    dst_y0 = src_y0 - y0
    output[
        dst_y0 : dst_y0 + (src_y1 - src_y0),
        dst_x0 : dst_x0 + (src_x1 - src_x0),
    ] = rgba[src_y0:src_y1, src_x0:src_x1]
    return output


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

    if failures:
        return "fail", failures + warnings
    if warnings:
        return "warn", warnings
    return "pass", []


def main() -> int:
    args = parse_args()
    for path in [args.output, args.alpha_out, args.preview_out, args.report_out]:
        ensure_parent(path)

    source = Image.open(args.input).convert("RGB")
    width, height = source.size
    if width < 64 or height < 32:
        raise SystemExit("source image is too small for paired recovery")
    if width % 2:
        # A one-pixel model/export discrepancy is safer to crop than to resize.
        source = source.crop((0, 0, width - 1, height))
        width -= 1

    panel_width = width // 2
    array = np.asarray(source, dtype=np.float32) / 255.0
    left = array[:, :panel_width]
    right = array[:, panel_width:]
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
    rgba, _residual, metrics = solve_rgba(left, aligned_right, valid, black, white)
    aspect = parse_aspect(args.aspect)
    final_rgba = fit_final_canvas(rgba, aspect, args.padding)

    Image.fromarray(to_u8(final_rgba), "RGBA").save(args.output)
    if args.alpha_out:
        Image.fromarray(to_u8(final_rgba[..., 3]), "L").save(args.alpha_out)
    if args.preview_out:
        save_preview(final_rgba, args.preview_out)

    grade, issues = grade_report(metrics, registration_score)
    report = {
        "input": str(args.input),
        "output": str(args.output),
        "source_size": [width, height],
        "panel_size": [panel_width, height],
        "final_size": [int(final_rgba.shape[1]), int(final_rgba.shape[0])],
        "requested_aspect": args.aspect,
        "estimated_black_rgb": [round(float(x), 6) for x in black],
        "estimated_white_rgb": [round(float(x), 6) for x in white],
        "backdrop_contrast": round(contrast, 6),
        "alignment_translation": {"x": dx, "y": dy},
        "registration_score": round(float(registration_score), 6),
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
