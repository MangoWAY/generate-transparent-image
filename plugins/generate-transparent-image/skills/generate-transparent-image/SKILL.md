---
name: generate-transparent-image
description: Generate robust transparent PNG assets from a user's text prompt or reference image using either a black/white paired source or a material-aware 2x2 black/white/opaque-core/soft-effect master. Recover alpha mathematically, preserve intended translucent detail, force semantic solid interiors opaque, validate on adversarial backgrounds, and preserve editable sources. Use for generating, extracting, cutting out, removing backgrounds, or iterating on raster transparency, especially mixed solid-plus-soft subjects, hair, fur, glass, smoke, glow, fabric, and liquids.
---

# Generate Transparent Image

Create a transparent raster asset from a prompt or reference image. Use a black/white pair for uniformly solid or uniformly soft subjects. Use one material-aware 2x2 master for mixed subjects that combine opaque bodies with translucent effects, then recover straight RGBA locally with `scripts/recover_alpha.py`.

This skill intentionally uses black and white instead of a chroma key. Do not replace this workflow with green, blue, or other colored backgrounds: generative models commonly spill those colors into the subject.

## Required outputs

Create a project-local directory such as `output/transparent-image/<asset-slug>/` and keep:

- `source-pair.png` or `source-2x2.png`: the untouched generated master
- `prompt.txt`: the normalized generation prompt
- `transparent.png`: final straight-alpha PNG
- `alpha.png`: recovered matte
- `preview.png`: checkerboard preview
- `preview-grid.png`: checker, black, white, green, and magenta validation views
- `report.json`: registration and quality diagnostics

For material-aware 2x2 recovery also keep:

- `soft-alpha.png`: alpha recovered only from black/white panels
- `soft-preview-grid.png`: adversarial preview before semantic core correction
- `opaque-core-mask.png`: registered and safely eroded solid-interior mask
- `soft-effect-mask.png`: registered semantic mask for retained translucent detail

Never leave a project deliverable only under `$CODEX_HOME/generated_images/`. Copy the selected generated source into the output directory before processing it.

## Workflow

### 1. Interpret the request

Accept either:

- a text prompt; or
- one or more attached/local reference images plus optional change instructions.

Treat a supplied image as a reference or edit target according to the user's wording. If a local image is not yet visible in conversation context, inspect it with `view_image` before calling the built-in image generator.

Extract the requested final aspect ratio. Default to the source panel ratio when none is given. The requested ratio applies to `transparent.png`, not necessarily to the full paired generation canvas.

Preserve the user's subject, style, colors, materials, viewpoint, and exact text. Add only production constraints needed for extraction.

Write an opacity plan before generation:

- `opaque`: skin, solid clothing, solid products, metal, shoes, and solid props;
- `soft`: hair fringe, fur edge, glass, water, smoke, fire, glow, ink wash, and sheer fabric;
- `background`: everything to remove.

Use the paired layout when one opacity behavior dominates. Use the material-aware 2x2 layout when opaque and soft regions must coexist, such as a character with ink, smoke, glow, or translucent cloth.

### 2. Build one master

Read [references/prompt-protocol.md](references/prompt-protocol.md), then normalize the user's request into `prompt.txt` using its paired-source block.

Use the built-in `image_gen` tool. Generate every panel in one image and one tool call. Never create panels through separate calls: independent generations do not share pixel geometry.

For ordinary paired recovery, use the existing 50/50 black-left/white-right protocol.

For mixed-material recovery, use the 2x2 protocol from `references/prompt-protocol.md`:

- top-left: subject over pure black;
- top-right: the identical subject over pure white;
- bottom-left: white opaque-core mask over pure black;
- bottom-right: white soft-effect mask over pure black.

Treat bottom masks as semantic guidance, not final cutout edges. Keep their geometry and panel-relative placement identical to the top subject. The recovery script registers them, erodes the core mask, and preserves top-pair alpha around fine boundaries.

For the ordinary paired layout require all of the following:

- one canvas split vertically at exactly 50%, with no gutter, border, divider, labels, or panel frames;
- pure black `#000000` on the left and pure white `#FFFFFF` on the right;
- one complete copy of the subject centered in each half;
- copies identical in pose, silhouette, scale, placement within their half, perspective, texture, material, lighting, and color;
- backgrounds treated as flat display layers that do not illuminate, tint, reflect in, or cast light onto the subject;
- no cast shadow, contact shadow, floor, reflection, watermark, or unrequested text;
- generous subject padding and no clipping.

For a supplied reference image, explicitly require high identity and geometry preservation. Label each input image's role.

Do not ask the generator for transparency. The generated artifact must remain an opaque technical master.

### 3. Recover RGBA

Copy the selected generation to `source-pair.png`. Run:

```bash
python3 <skill-dir>/scripts/recover_alpha.py \
  --input <output-dir>/source-pair.png \
  --output <output-dir>/transparent.png \
  --alpha-out <output-dir>/alpha.png \
  --preview-out <output-dir>/preview.png \
  --preview-grid-out <output-dir>/preview-grid.png \
  --report-out <output-dir>/report.json \
  --aspect <requested-ratio-or-source> \
  --strict
```

For a material-aware 2x2 master run:

```bash
python3 <skill-dir>/scripts/recover_alpha.py \
  --layout material-2x2 \
  --input <output-dir>/source-2x2.png \
  --output <output-dir>/transparent.png \
  --alpha-out <output-dir>/alpha.png \
  --soft-alpha-out <output-dir>/soft-alpha.png \
  --core-mask-out <output-dir>/opaque-core-mask.png \
  --soft-mask-out <output-dir>/soft-effect-mask.png \
  --preview-out <output-dir>/preview.png \
  --preview-grid-out <output-dir>/preview-grid.png \
  --soft-preview-grid-out <output-dir>/soft-preview-grid.png \
  --report-out <output-dir>/report.json \
  --aspect <requested-ratio-or-source> \
  --strict
```

The script requires Pillow and NumPy. Check the active `python3` before recovery. If either package is missing, use a workspace Python that already provides them; otherwise ask the user before installing from `<skill-dir>/requirements.txt` into an isolated environment. Do not silently modify the user's global Python environment.

Omit `--aspect` when the user did not request one. Accepted forms include `1:1`, `4:5`, `16:9`, or a decimal. The script estimates actual backdrop colors from the border, aligns the right copy to the left, solves alpha against those backdrops, and writes a diagnostic report. Its default `--edge-cleanup auto` removes faint frames and seam residue only inside the guaranteed blank panel margin. Use `--edge-cleanup aggressive` when a visible outer line survives; use `off` only when the requested subject intentionally touches the panel edge.

### 4. Inspect and gate quality

Inspect the source master, alpha and mask outputs, `preview.png`, and `preview-grid.png`; do not trust the exit code alone. Read `report.json`.

- Judge primarily by visible usability, not mathematical perfection. View `preview.png` at the intended display size; for game VFX and icons, check at 64, 128, and 256 px when the user did not specify a size. Zoom in only to diagnose an artifact already visible at one of those sizes.
- `pass`: deliver when visual inspection succeeds.
- `warn`: deliver when the asset looks clean at intended size. Do not regenerate solely because of a warning or because tiny edge/glow differences are visible only when enlarged.
- `fail`: regenerate only when visual inspection confirms a core defect such as double edges, broken silhouette, obvious background tint, large opaque residue, clipping, or missing subject detail. Try at most two corrective generations, retain every attempt, and choose the most usable result.

For mixed-material assets, specifically verify that saturated green and magenta do not show through opaque interiors while intended smoke, ink, glow, glass, fur, and sheer edges remain soft.

Treat diagnostic metrics as supporting evidence. High values help locate a problem but do not override a clean intended-size preview. Target confirmed corrections narrowly:

- high `registration_score`: demand exact duplicate geometry, placement, and camera; remove decorative layout language;
- high `residual_p95`: demand identical subject color/material and no background-dependent lighting or spill;
- high `foreground_disagreement_p90`: demand the black and white panels differ only in background pixels;
- clipping or excessive coverage: demand more padding and a smaller centered subject;
- noisy background alpha: demand perfectly uniform `#000000` and `#FFFFFF` backgrounds.
- nonzero `edge_cleanup_line_runs_removed`: inspect the outer margin; the script removed one or more long frame/seam traces without eroding the center matte;
- low `semantic_support_recall`: demand masks cover every retained core and soft region;
- high `core_outside_pair_support_fraction`: demand identical mask geometry and placement;
- high `core_soft_overlap_fraction`: separate solid regions from intended translucent effects.

Do not chase pixel-level perfection from a generative source. Minor spark drift, subpixel edge differences, and small glow-intensity changes are acceptable when they disappear at intended size. Do not hide a genuinely visible defect by aggressively eroding or blurring the matte; fine hair, glass, smoke, glow, and translucent edges are the reason to use this workflow.

### 5. Deliver and preserve iteration state

Show the final transparent image and report its saved path. Mention a quality warning only when it predicts a visible limitation in normal use; do not surface harmless diagnostic noise.

Keep the paired master and prompt so follow-up edits remain coherent:

- For only aspect ratio, padding, or crop changes, rerun `recover_alpha.py` on the same `source-pair.png`.
- For semantic or visual changes, edit/regenerate from `source-pair.png` and the previous `prompt.txt`. Repeat all paired-source invariants and state the single requested change.
- Never use `transparent.png` as the sole semantic edit source when a paired or 2x2 master is available.
- Save iterations non-destructively as `attempt-02/`, `attempt-03/`, or a new versioned asset directory.

## Recovery model

For aligned composites over black backdrop `B0` and white backdrop `B1`:

```text
C0 = alpha * F + (1 - alpha) * B0
C1 = alpha * F + (1 - alpha) * B1
```

The script estimates `B0` and `B1` from the image border and solves the shared scalar alpha by least squares across RGB. It then clears the guaranteed blank outer guard, suppresses weak edge-band noise, and removes only thin long frame/seam runs near panel boundaries. For material-aware 2x2 inputs it registers the semantic masks, erodes the opaque-core boundary, forces only safe solid interiors to alpha 1, and replaces their RGB with the aligned black/white panel average. Soft effects retain pair-recovered alpha. Generative panels are rarely pixel-perfect, so diagnostics are intentionally tolerant and adversarial-background inspection is the final gate.
