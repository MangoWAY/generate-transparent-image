# Paired-source prompt protocol

Use this reference when constructing or correcting the model prompt. Keep the user's creative request intact and append only the constraints below.

## Base block

```text
Asset type: transparent cutout source pair
Primary request: <user request, preserving requested content and style>
Reference images: <each image and its role, if any>
Final transparent canvas ratio: <ratio or "match source panel">

Paired-source construction:
Create one opaque technical two-panel source image. Split the canvas vertically at exactly 50%.
The entire left half must be perfectly uniform pure black #000000.
The entire right half must be perfectly uniform pure white #FFFFFF.
There is no gutter, divider, border, frame, label, caption, or margin between the halves.

Place one complete copy of the requested subject in each half. The right subject must be an exact duplicate of the left subject: identical pose, expression, silhouette, scale, position relative to its panel, camera, perspective, geometry, texture, material, fine detail, lighting, opacity, and RGB color. Only the background changes.

Treat black and white as flat digital compositing layers, not physical environments. They must not illuminate, tint, recolor, reflect in, spill onto, rim-light, or change the exposure or contrast of the subject. Preserve natural subject colors identically on both sides.

Keep generous clear padding around both copies. No clipping. No floor plane, cast shadow, contact shadow, reflection, atmospheric background, watermark, signature, or text unless the user explicitly requested text on the subject.
```

## Reference-image addition

Append this when the user supplies a visual reference:

```text
Use the supplied image as the identity and geometry reference. Preserve the subject's defining shape, viewpoint, proportions, materials, colors, markings, and any requested exact text. Reconstruct the paired source without carrying over the original scene background. Both panel copies must match the same reference in exactly the same way.
```

## Aspect-ratio handling

The final ratio describes the transparent deliverable, not the two-panel canvas. Keep each subject inside a central safe area so post-processing can crop or add transparent padding to the requested ratio.

For a wide requested output, do not demand a dangerously wide paired canvas. Use a supported generation canvas and reserve adequate safe area around each duplicate; the recovery script will create the exact final ratio.

## Correction snippets

Use only the relevant correction after a failed attempt.

Geometry mismatch:

```text
Correction: the two subjects drifted. Render the right subject as the same duplicate, not a variation. Lock pose, silhouette, scale, panel-relative coordinates, camera, and all small details. Change background pixels only.
```

Color or lighting contamination:

```text
Correction: the background changed the subject rendering. Use identical RGB values, material response, exposure, and edge colors for both copies. The black and white layers provide no illumination and create no spill, rim light, reflection, or contrast adaptation.
```

Backdrop contamination:

```text
Correction: make every non-subject pixel on the left exactly #000000 and every non-subject pixel on the right exactly #FFFFFF. Remove gradients, texture, floor, shadows, glow in the background, borders, and compression-like decoration.
```

Clipping:

```text
Correction: reduce both copies equally and center them within their halves with generous identical padding on all sides. Keep the complete subject visible.
```
