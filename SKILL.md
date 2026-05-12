---
name: paper-to-md
description: Convert academic journal PDFs (Nature, Science, IPCC reports, journal articles, supplementary materials, preprints) into clean single-file Markdown with figures and equations rendered as PNG images at their correct positions. Use this skill whenever a user references a .pdf file in any paper-reading context, asks to convert / extract / read / 转 / 读 / 提取 a paper, mentions journal articles or supplementary information that's hard to read, or wants to summarize, annotate, cite, or compare papers. Even when the user only says "read this paper" or "I downloaded a new article", invoke this skill — it solves two-column layouts, multi-page paragraph wrap, header/footer noise, hyphenation, captions split across columns, math equations rendered as fragments, and packed reference lists, none of which `markitdown` or `pdftotext` handle. Do not use for editable Office documents (.docx/.pptx) or HTML pages.
---

# paper-to-md

Convert academic journal PDFs into Markdown with figures and equations rendered as PNGs in reading order.

## Why this exists

`markitdown`, `pdftotext`, and `pymupdf get_text("text")` all fail on academic PDFs in different ways:

- **`pdftotext -layout`** preserves visual columns → reads each scanline left-to-right, garbling reading order line-by-line.
- **`pdftotext`** (no flag) flows columns OK, but produces no figures, no headings, and breaks hyphenated words (`eco-\nsystems`).
- **`markitdown`** handles two-column flow but extracts zero images and doesn't fix hyphenation.
- **`pymupdf get_text("text", sort=True)`** has the same scanline problem as `-layout`.

The bundled `scripts/extract.py` is purpose-built for two-column journal articles and their Supplementary Information. It uses `pymupdf` (`import fitz`) and Python stdlib only.

## Workflow

### 1. Identify the input PDF(s)

Look at the user's message and the working directory. Most journal-paper sessions have a folder containing one main PDF and an SI PDF; if the user says "convert this paper" without naming a file, list the `.pdf` files present and ask which to convert. If they name the main paper, also offer to do the SI in the same run — they share a `figures/` directory naturally.

### 2. Run the extractor

Single PDF (output goes next to the PDF):

```bash
python3 "${SKILL_DIR}/scripts/extract.py" "<path/to/paper.pdf>"
```

Main paper + SI together (shared `figures/`):

```bash
python3 "${SKILL_DIR}/scripts/extract.py" "<main.pdf>" "<SI.pdf>"
```

Explicit overrides (rarely needed):

```bash
python3 "${SKILL_DIR}/scripts/extract.py" "<paper.pdf>" \
    -o out.md --dpi 240 --prefix custom --figures-dir /tmp/figs
```

Flags:
- `-o, --output PATH` — output markdown path (single-file mode only)
- `--dpi INT` — figure/equation render DPI (default 180; 240 for print-quality)
- `--prefix STR` — figure filename prefix (default: `SI` for SI-prefixed PDFs, sanitized basename otherwise)
- `--figures-dir PATH` — where to write figures (default: `figures/` next to the first PDF)

Dependency: `pymupdf` (`pip install pymupdf` if missing). Everything else is stdlib.

### 3. Spot-check a content-heavy page

The title page usually looks fine even when extraction is broken. Always open the markdown around page 3–5 (or any methods page) and verify:

- Body paragraphs read as continuous prose, not interleaved fragments from two columns.
- Figure captions follow figures; no stray italic single-character lines between them.
- Section headings (`## Methods`, `### Data`, etc.) appear at expected places.
- Numbered references (`1. Stechow ...`, `2. Stern ...`) sit each on their own line at the bottom.

If something looks off, fix the underlying logic in `scripts/extract.py` — don't ad-hoc patch the output `.md`. The user has explicitly stated this preference: fixes should land in the script so they apply to future papers too.

### 4. Optional: cross-validate against publisher HTML

If the article's HTML page is in the same folder (`<title>_<journal>.html`), extracting its `<div class="c-article-section">` body via BeautifulSoup gives a strong structural ground truth. Then:

```bash
diff <(grep "^## " out.md) <(grep "^## " html_body.md)
```

Section-heading parity is a quick correctness signal.

### 5. Report

Tell the user: line count, figure count, output paths. If they're in VSCode, they can preview with `Ctrl+Shift+V`.

## What the extractor handles

The script is documented inline; the short version of what it does that simpler tools don't:

- **Two-column reading order** via bbox classification (`left/right/full` against page midline)
- **Body font auto-detection** — counts spans, picks dominant `(size, font)` as body; everything else is classified relative to that, so it works on Nature, Science, IPCC, and InDesign-set SI without per-doc tuning
- **First-line indent splitting** within long PDF blocks that hold multiple paragraphs
- **Cross-page / cross-column paragraph merging** when prev ends mid-sentence and next starts lowercase
- **Caption merging across columns** (`y_overlap > 5`) and absorption of body wrap-around
- **Figure region expansion** — iteratively pulls in axis labels and panel letters within ±60pt vertical / ±30pt horizontal until stable
- **Vector figure detection** via `get_drawings()` rects (Nature uses vector graphics; raster-only detection misses ~70% of figures)
- **Math equations as PNGs** — clusters small/small_caption items by 18pt vertical / 220pt horizontal proximity, renders each cluster as a discrete inline image
- **Header/footer noise filter** — three-layer (regex + font + position)
- **Hyphenation repair** with a short-glue set (`differ-\nences → differences`; `Medium-\nto → Medium- to`)
- **Packed reference lists** split on `\d+\.\s+[A-Z]` boundaries when ≥4 numbered entries detected

## Known limitations

See `references/known_limitations.md` for cosmetic issues that aren't fully solved (rotated axis labels appearing as stray italic lines, equation continuations classified as small/italic, occasional author-order interleaving). All are low-severity — none impair reading.

## Fixing root causes

When the user reports a problem with a paper's output, fix the underlying script behavior in `scripts/extract.py` so the fix applies to future papers too. Don't ad-hoc patch the output file. The user prefers re-runnable improvements over one-off cleanups.
