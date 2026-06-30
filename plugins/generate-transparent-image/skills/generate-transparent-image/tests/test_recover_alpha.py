from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "recover_alpha.py"
SPEC = importlib.util.spec_from_file_location("recover_alpha", SCRIPT)
assert SPEC and SPEC.loader
RECOVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVER)


def synthetic_subject(size: int = 72) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    yy, xx = np.indices((size, size))
    core = (xx >= 24) & (xx < 49) & (yy >= 16) & (yy < 57)
    effect = ((xx - 17) ** 2 + (yy - 35) ** 2 < 11**2) & ~core
    alpha = np.zeros((size, size), dtype=np.float32)
    alpha[core] = 0.58  # Deliberately wrong for a semantically solid region.
    alpha[effect] = 0.36

    foreground = np.zeros((size, size, 3), dtype=np.float32)
    foreground[core] = np.array([0.82, 0.70, 0.60], dtype=np.float32)
    foreground[effect] = np.array([0.18, 0.55, 0.95], dtype=np.float32)
    left = alpha[..., None] * foreground
    right = alpha[..., None] * foreground + (1.0 - alpha[..., None])
    return left, right, core.astype(np.float32), effect.astype(np.float32)


class RecoveryTests(unittest.TestCase):
    def test_partial_alpha_color_is_recovered_without_white_fringe(self) -> None:
        size = 96
        yy, xx = np.indices((size, size))
        distance = np.sqrt((xx - 48) ** 2 + (yy - 48) ** 2)
        alpha = np.clip((34.0 - distance) / 8.0, 0.0, 1.0).astype(np.float32)
        foreground = np.zeros((size, size, 3), dtype=np.float32)
        foreground[..., 0] = 0.88
        foreground[..., 1] = 0.17 + 0.25 * (xx / size)
        foreground[..., 2] = 0.08 + 0.35 * (yy / size)
        black = np.array([0.01, 0.02, 0.03], dtype=np.float32)
        white = np.array([0.96, 0.97, 0.98], dtype=np.float32)
        transmission = 1.0 - alpha[..., None]
        left = alpha[..., None] * foreground + transmission * black
        right = alpha[..., None] * foreground + transmission * white

        rgba, _residual, metrics = RECOVER.solve_rgba(
            left, right, np.ones(alpha.shape, dtype=bool), black, white
        )

        edge = (alpha > 0.05) & (alpha < 0.95)
        self.assertLess(float(np.max(np.abs(rgba[..., 3][edge] - alpha[edge]))), 1e-5)
        self.assertLess(
            float(np.max(np.abs(rgba[..., :3][edge] - foreground[edge]))), 1e-4
        )
        dark = np.full((size, size, 3), 0.04, dtype=np.float32)
        expected = foreground * alpha[..., None] + dark * (1.0 - alpha[..., None])
        actual = RECOVER.composite_over(rgba, dark)
        self.assertLess(float(np.max(np.abs(actual[edge] - expected[edge]))), 1e-4)
        self.assertLess(metrics["residual_p95"], 1e-5)

    def test_translation_alignment_recovers_shifted_pair(self) -> None:
        size = 96
        yy, xx = np.indices((size, size))
        alpha = np.clip(
            (30.0 - np.sqrt((xx - 48) ** 2 + (yy - 48) ** 2)) / 4.0,
            0.0,
            1.0,
        ).astype(np.float32)
        foreground = np.dstack(
            [
                0.2 + 0.7 * xx / size,
                0.1 + 0.8 * yy / size,
                0.5 + 0.25 * np.sin(xx / 3.0),
            ]
        ).astype(np.float32)
        left = alpha[..., None] * foreground
        right = alpha[..., None] * foreground + (1.0 - alpha[..., None])
        white = np.ones(3, dtype=np.float32)
        shifted_right, _valid = RECOVER.shifted(right, 4, -3, white)
        dx, dy, score = RECOVER.find_translation(
            left,
            shifted_right,
            np.zeros(3, dtype=np.float32),
            white,
            max_shift_fraction=0.10,
        )
        self.assertEqual((dx, dy), (-4, 3))
        self.assertLess(score, 0.01)

    def test_aspect_ratio_validation_rejects_zero_and_extreme_values(self) -> None:
        for value in ["1:0", "1/0", "0:1", "21:1", "nan", "inf"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                RECOVER.parse_aspect(value)
        self.assertAlmostEqual(RECOVER.parse_aspect("16:9"), 16 / 9)
        self.assertAlmostEqual(RECOVER.parse_aspect("4/5"), 0.8)

    def test_final_canvas_preserves_content_and_exact_requested_ratio(self) -> None:
        rgba = np.zeros((80, 100, 4), dtype=np.float32)
        rgba[20:60, 35:65, :3] = np.array([0.9, 0.2, 0.1], dtype=np.float32)
        rgba[20:60, 35:65, 3] = 1.0
        final = RECOVER.fit_final_canvas(rgba, 16 / 9, padding=0.10)
        self.assertLessEqual(abs(final.shape[1] - final.shape[0] * 16 / 9), 1.0)
        self.assertEqual(int((final[..., 3] > 0.5).sum()), 40 * 30)
        self.assertTrue(np.all(final[0, :, 3] == 0.0))
        self.assertTrue(np.all(final[-1, :, 3] == 0.0))

    def test_low_contrast_semantic_mask_is_rejected(self) -> None:
        mask = np.full((64, 64, 3), 0.42, dtype=np.float32)
        mask[20:44, 20:44] = 0.50
        with self.assertRaisesRegex(ValueError, "lacks a clear white-on-black signal"):
            RECOVER.decode_semantic_mask(mask)

    def test_strict_cli_rejects_non_black_white_backdrops(self) -> None:
        master = np.full((64, 128, 3), 0.45, dtype=np.float32)
        master[:, 64:] = 0.55
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "bad-backdrops.png"
            Image.fromarray(RECOVER.to_u8(master), "RGB").save(source)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(source),
                    "--output",
                    str(root / "out.png"),
                    "--strict",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not sufficiently black and white", result.stderr)

    def test_cli_crops_one_pixel_export_discrepancy(self) -> None:
        left, right, _core, _effect = synthetic_subject(72)
        master = np.concatenate([left, right], axis=1)
        master = np.pad(master, ((0, 0), (0, 1), (0, 0)), constant_values=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "odd-width.png"
            report_path = root / "report.json"
            Image.fromarray(RECOVER.to_u8(master), "RGB").save(source)
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(source),
                    "--output",
                    str(root / "transparent.png"),
                    "--report-out",
                    str(report_path),
                    "--no-align",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["source_size"], [144, 72])
            self.assertEqual(report["panel_size"], [72, 72])

    def test_edge_cleanup_removes_panel_frame_without_eroding_subject(self) -> None:
        size = 160
        rgba = np.zeros((size, size, 4), dtype=np.float32)
        rgba[45:120, 58:104, :3] = np.array([0.8, 0.4, 0.2], dtype=np.float32)
        rgba[45:120, 58:104, 3] = 0.75
        rgba[2, :, 3] = 0.05
        rgba[:, 3, 3] = 0.07
        rgba[:, -4, 3] = 0.42

        cleaned, removed, metrics = RECOVER.apply_edge_cleanup(rgba, "auto")

        self.assertTrue(np.all(cleaned[2, :, 3] == 0.0))
        self.assertTrue(np.all(cleaned[:, 3, 3] == 0.0))
        self.assertTrue(np.all(cleaned[:, -4, 3] == 0.0))
        self.assertTrue(np.allclose(cleaned[45:120, 58:104], rgba[45:120, 58:104]))
        self.assertTrue(np.all(removed[:, -4]))
        self.assertGreaterEqual(metrics["edge_cleanup_line_runs_removed"], 1.0)
        self.assertGreater(metrics["edge_cleanup_max_alpha_removed"], 0.4)

    def test_edge_cleanup_can_be_disabled(self) -> None:
        rgba = np.zeros((64, 64, 4), dtype=np.float32)
        rgba[:, 1, 3] = 0.3
        cleaned, removed, metrics = RECOVER.apply_edge_cleanup(rgba, "off")
        self.assertTrue(np.array_equal(cleaned, rgba))
        self.assertFalse(removed.any())
        self.assertEqual(metrics["edge_cleanup_pixels_removed"], 0.0)

    def test_core_is_forced_opaque_while_soft_effect_stays_soft(self) -> None:
        left, right, core, effect = synthetic_subject()
        valid = np.ones(core.shape, dtype=bool)
        rgba, _residual, _metrics = RECOVER.solve_rgba(
            left,
            right,
            valid,
            np.zeros(3, dtype=np.float32),
            np.ones(3, dtype=np.float32),
        )
        soft_before = rgba[..., 3].copy()
        refined, safe_core, _soft, metrics = RECOVER.apply_material_masks(
            rgba, left, right, core, effect, core_erode=1
        )

        core_pixels = safe_core >= 0.5
        effect_pixels = effect >= 0.5
        self.assertGreater(int(core_pixels.sum()), 0)
        self.assertTrue(np.allclose(refined[..., 3][core_pixels], 1.0))
        self.assertTrue(
            np.allclose(refined[..., 3][effect_pixels], soft_before[effect_pixels], atol=1e-5)
        )
        self.assertLess(metrics["core_alpha_p05_before"], 0.70)
        self.assertEqual(metrics["core_alpha_p05_after"], 1.0)
        self.assertGreater(metrics["core_low_alpha_fraction_before"], 0.95)
        self.assertEqual(metrics["core_low_alpha_fraction_after"], 0.0)

    def test_material_2x2_cli_emits_masks_and_adversarial_preview(self) -> None:
        left, right, core, effect = synthetic_subject()
        mask_core = np.repeat(core[..., None], 3, axis=2)
        mask_effect = np.repeat(effect[..., None], 3, axis=2)
        top = np.concatenate([left, right], axis=1)
        bottom = np.concatenate([mask_core, mask_effect], axis=1)
        master = np.concatenate([top, bottom], axis=0)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source-2x2.png"
            Image.fromarray(RECOVER.to_u8(master), "RGB").save(source)
            command = [
                sys.executable,
                str(SCRIPT),
                "--layout",
                "material-2x2",
                "--input",
                str(source),
                "--output",
                str(root / "transparent.png"),
                "--alpha-out",
                str(root / "alpha.png"),
                "--soft-alpha-out",
                str(root / "soft-alpha.png"),
                "--core-mask-out",
                str(root / "opaque-core-mask.png"),
                "--soft-mask-out",
                str(root / "soft-effect-mask.png"),
                "--preview-grid-out",
                str(root / "preview-grid.png"),
                "--soft-preview-grid-out",
                str(root / "soft-preview-grid.png"),
                "--report-out",
                str(root / "report.json"),
                "--core-erode",
                "1",
                "--no-align",
                "--strict",
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)

            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["layout"], "material-2x2")
            self.assertEqual(report["core_alpha_p05_after"], 1.0)
            self.assertEqual(report["edge_cleanup"], "auto")
            for name in [
                "transparent.png",
                "alpha.png",
                "soft-alpha.png",
                "opaque-core-mask.png",
                "soft-effect-mask.png",
                "preview-grid.png",
                "soft-preview-grid.png",
            ]:
                self.assertTrue((root / name).is_file(), name)

    def test_soft_mask_wins_overlap_with_opaque_core(self) -> None:
        left, right, core, effect = synthetic_subject()
        valid = np.ones(core.shape, dtype=bool)
        rgba, _residual, _metrics = RECOVER.solve_rgba(
            left,
            right,
            valid,
            np.zeros(3, dtype=np.float32),
            np.ones(3, dtype=np.float32),
        )
        overlap_region = (core >= 0.5) & (np.indices(core.shape)[1] < 31)
        effect_with_overlap = effect.copy()
        effect_with_overlap[overlap_region] = 1.0
        alpha_before = rgba[..., 3].copy()

        refined, safe_core, _soft, metrics = RECOVER.apply_material_masks(
            rgba, left, right, core, effect_with_overlap, core_erode=1
        )
        self.assertTrue(np.all(safe_core[overlap_region] == 0.0))
        self.assertTrue(
            np.allclose(refined[..., 3][overlap_region], alpha_before[overlap_region])
        )
        self.assertGreater(metrics["core_soft_overlap_fraction"], 0.0)

    def test_semantic_mask_translation_is_recovered(self) -> None:
        _left, _right, core, effect = synthetic_subject()
        target = np.maximum(core, effect)
        shifted_core, _ = RECOVER.shifted_2d(core, 3, -2)
        shifted_effect, _ = RECOVER.shifted_2d(effect, 3, -2)
        dx, dy, score = RECOVER.find_mask_translation(
            target, shifted_core, shifted_effect, max_shift_fraction=0.10
        )
        self.assertEqual((dx, dy), (-3, 2))
        self.assertLess(score, 0.05)

    def test_paired_layout_remains_supported(self) -> None:
        left, right, _core, _effect = synthetic_subject()
        master = np.concatenate([left, right], axis=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source-pair.png"
            Image.fromarray(RECOVER.to_u8(master), "RGB").save(source)
            command = [
                sys.executable,
                str(SCRIPT),
                "--input",
                str(source),
                "--output",
                str(root / "transparent.png"),
                "--report-out",
                str(root / "report.json"),
                "--no-align",
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["layout"], "paired")
            self.assertTrue((root / "transparent.png").is_file())


if __name__ == "__main__":
    unittest.main()
