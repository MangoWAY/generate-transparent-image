---
name: generate-transparent-image
description: Generate robust transparent PNG assets from a user's text prompt or reference image by creating one exact 50/50 black-and-white paired source, registering both copies, recovering alpha mathematically, validating contamination and mismatch, and preserving the paired source for later edits. Use when a user asks to generate, extract, cut out, remove the background from, or iteratively modify a raster image with transparency, especially hair, fur, glass, smoke, glow, fabric, liquids, or other subjects that ordinary segmentation or chroma keying handles poorly.
---

# Generate Transparent Image

Create a transparent raster asset from a prompt or reference image. Use one model-generated black/white paired source as the editable master, then recover straight RGBA locally with `scripts/recover_alpha.py`.

This skill intentionally uses black and white instead of a chroma key. Do not replace this workflow with green, blue, or other colored backgrounds: generative models commonly spill those colors into the subject.

## Required outputs

Create a project-local directory such as `output/transparent-image/<asset-slug>/` and keep:

- `source-pair.png`: the untouched generated 50/50 master
- `prompt.txt`: the normalized generation prompt
- `transparent.png`: final straight-alpha PNG
- `alpha.png`: recovered matte
- `preview.png`: checkerboard preview
- `report.json`: registration and quality diagnostics

Never leave a project deliverable only under `$CODEX_HOME/generated_images/`. Copy the selected generated source into the output directory before processing it.

## Workflow

### 1. Interpret the request

Accept either:

- a text prompt; or
- one or more attached/local reference images plus optional change instructions.

Treat a supplied image as a reference or edit target according to the user's wording. If a local image is not yet visible in conversation context, inspect it with `view_image` before calling the built-in image generator.

Extract the requested final aspect ratio. Default to the source panel ratio when none is given. The requested ratio applies to `transparent.png`, not necessarily to the full paired generation canvas.

Preserve the user's subject, style, colors, materials, viewpoint, and exact text. Add only production constraints needed for extraction.

### 2. Build one paired master

Read [references/prompt-protocol.md](references/prompt-protocol.md), then normalize the user's request into `prompt.txt` using its paired-source block.

Use the built-in `image_gen` tool. Generate both copies in one image and one tool call. Never create the black and white versions through separate calls: independent generations do not share pixel geometry.

Require all of the following:

- one canvas split vertically at exactly 50%, with no gutter, border, divider, labels, or panel frames;
- pure black `#000000` on the left and pure white `#FFFFFF` on the right;
- one complete copy of the subject centered in each half;
- copies identical in pose, silhouette, scale, placement within their half, perspective, texture, material, lighting, and color;
- backgrounds treated as flat display layers that do not illuminate, tint, reflect in, or cast light onto the subject;
- no cast shadow, contact shadow, floor, reflection, watermark, or unrequested text;
- generous subject padding and no clipping.

For a supplied reference image, explicitly require high identity and geometry preservation. Label each input image's role.

Do not ask the generator for transparency. The generated artifact must remain an opaque black/white paired source.

### 3. Recover RGBA

Copy the selected generation to `source-pair.png`. Run:

```bash
python3 <skill-dir>/scripts/recover_alpha.py \
  --input <output-dir>/source-pair.png \
  --output <output-dir>/transparent.png \
  --alpha-out <output-dir>/alpha.png \
  --preview-out <output-dir>/preview.png \
  --report-out <output-dir>/report.json \
  --aspect <requested-ratio-or-source> \
  --strict
```

The script requires Pillow and NumPy. If the active `python3` lacks them, use the bundled workspace Python reported by `codex_app__load_workspace_dependencies`. Do not silently install packages into the user's environment.

Omit `--aspect` when the user did not request one. Accepted forms include `1:1`, `4:5`, `16:9`, or a decimal. The script estimates actual backdrop colors from the border, aligns the right copy to the left, solves alpha against those backdrops, and writes a diagnostic report.

### 4. Inspect and gate quality

Inspect `source-pair.png`, `alpha.png`, and `preview.png`; do not trust the exit code alone. Read `report.json`.

- Judge primarily by visible usability, not mathematical perfection. View `preview.png` at the intended display size; for game VFX and icons, check at 64, 128, and 256 px when the user did not specify a size. Zoom in only to diagnose an artifact already visible at one of those sizes.
- `pass`: deliver when visual inspection succeeds.
- `warn`: deliver when the asset looks clean at intended size. Do not regenerate solely because of a warning or because tiny edge/glow differences are visible only when enlarged.
- `fail`: regenerate only when visual inspection confirms a core defect such as double edges, broken silhouette, obvious background tint, large opaque residue, clipping, or missing subject detail. Try at most two corrective generations, retain every attempt, and choose the most usable result.

Treat diagnostic metrics as supporting evidence. High values help locate a problem but do not override a clean intended-size preview. Target confirmed corrections narrowly:

- high `registration_score`: demand exact duplicate geometry, placement, and camera; remove decorative layout language;
- high `residual_p95`: demand identical subject color/material and no background-dependent lighting or spill;
- high `foreground_disagreement_p90`: demand the black and white panels differ only in background pixels;
- clipping or excessive coverage: demand more padding and a smaller centered subject;
- noisy background alpha: demand perfectly uniform `#000000` and `#FFFFFF` backgrounds.

Do not chase pixel-level perfection from a generative source. Minor spark drift, subpixel edge differences, and small glow-intensity changes are acceptable when they disappear at intended size. Do not hide a genuinely visible defect by aggressively eroding or blurring the matte; fine hair, glass, smoke, glow, and translucent edges are the reason to use this workflow.

### 5. Deliver and preserve iteration state

Show the final transparent image and report its saved path. Mention a quality warning only when it predicts a visible limitation in normal use; do not surface harmless diagnostic noise.

Keep the paired master and prompt so follow-up edits remain coherent:

- For only aspect ratio, padding, or crop changes, rerun `recover_alpha.py` on the same `source-pair.png`.
- For semantic or visual changes, edit/regenerate from `source-pair.png` and the previous `prompt.txt`. Repeat all paired-source invariants and state the single requested change.
- Never use `transparent.png` as the sole semantic edit source when `source-pair.png` is available.
- Save iterations non-destructively as `attempt-02/`, `attempt-03/`, or a new versioned asset directory.

## Recovery model

For aligned composites over black backdrop `B0` and white backdrop `B1`:

```text
C0 = alpha * F + (1 - alpha) * B0
C1 = alpha * F + (1 - alpha) * B1
```

The script estimates `B0` and `B1` from the image border and solves the shared scalar alpha by least squares across RGB. Channel residuals become a contamination/confidence signal. Generative pairs are rarely pixel-perfect, so diagnostics are intentionally tolerant and visual inspection at intended size is the final gate. Retry only when pair differences create a visible defect.
