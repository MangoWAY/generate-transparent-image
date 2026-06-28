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

## Material-aware 2x2 block

Use this instead of the base paired block when the asset combines opaque subjects with retained translucent effects.

```text
Asset type: material-aware transparent cutout source master
Primary request: <user request, preserving requested content and style>
Reference images: <each image and its role, if any>
Final transparent canvas ratio: <ratio or "match source panel">

Opacity plan:
Opaque core: <skin, solid clothing, solid props, metal, product body, and other regions that must block every background>
Soft effects: <hair fringe, fur edge, glass, water, smoke, fire, glow, ink wash, sheer fabric, and other intentionally translucent regions>
Remove: <scene, floor, detached text, and all other background content>

Material-aware 2x2 construction:
Create one opaque square technical source image divided into four exactly equal quadrants. There is no gutter, divider, border, frame, label, caption, or margin between quadrants.

Top-left quadrant: render the complete retained subject over a perfectly uniform pure black #000000 background.
Top-right quadrant: render the exact same subject over a perfectly uniform pure white #FFFFFF background.
Bottom-left quadrant: render only the opaque-core semantic mask. Use perfectly uniform pure white #FFFFFF for opaque core regions and perfectly uniform pure black #000000 everywhere else.
Bottom-right quadrant: render only the soft-effect semantic mask. Use perfectly uniform pure white #FFFFFF for retained translucent effects and their fine boundary details, and perfectly uniform pure black #000000 everywhere else.

Construct the subject once and reuse identical geometry in all four quadrants. Lock identity, pose, silhouette, scale, panel-relative coordinates, camera, perspective, anatomy, texture placement, props, particles, and every fine detail. The top panels differ only in background pixels. The two mask panels describe the same geometry without shadows, texture, gray shading, holes, labels, or decorative marks.

Treat black and white top backgrounds as flat digital compositing layers. They do not illuminate, tint, recolor, reflect in, spill onto, rim-light, or change exposure or contrast. Preserve identical subject RGB colors and opacity appearance in both top panels.

The opaque-core mask must exclude hair fringe, antialiased outer edges, fur tips, glass, water, smoke, fire, glow, ink wash, sheer fabric, and other soft regions. The soft-effect mask must include those retained soft regions and may include a narrow boundary fringe around the solid subject. Do not include removed background content in either mask.

Keep generous identical padding around the full retained subject in every quadrant. No clipping. No floor, cast shadow, contact shadow, reflection, atmospheric scene, watermark, signature, or unrequested text.
```

## Reference-image addition

Append this when the user supplies a visual reference:

```text
Use the supplied image as the identity and geometry reference. Preserve the subject's defining shape, viewpoint, proportions, materials, colors, markings, and any requested exact text. Reconstruct the paired source without carrying over the original scene background. Both panel copies must match the same reference in exactly the same way.
```

For a material-aware 2x2 source, apply identity and geometry preservation to all four quadrants. The bottom masks are semantic maps for the same top-panel subject, not newly composed silhouettes.

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

Mask leakage or overlap:

```text
Correction: rebuild both bottom semantic masks from the exact top-panel geometry. The opaque-core mask contains only fully solid interior materials. The soft-effect mask contains only retained translucent effects and fine soft boundaries. Use pure binary #000000 and #FFFFFF with no gray texture, lighting, shadow, labels, or background remnants.
```

Clipping:

```text
Correction: reduce both copies equally and center them within their halves with generous identical padding on all sides. Keep the complete subject visible.
```
