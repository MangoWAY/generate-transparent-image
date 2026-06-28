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


class MaterialAwareRecoveryTests(unittest.TestCase):
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
