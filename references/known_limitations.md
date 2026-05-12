# Known limitations of `extract.py`

Cosmetic issues that aren't fully solved. None impair reading; all are documented here so the model doesn't waste cycles trying to fix them every run.

| Issue | Where it appears | Severity |
|---|---|---|
| Stray axis-label fragment `*Additional emission reduction (%)*` between Fig 3 caption and Fig 4 image. | A rotated y-axis label whose bbox doesn't overlap the figure's expanded region. | Low — one stray italic line, doesn't impair reading. |
| Math text continuations classified as `small` because they begin with subscripts (e.g. `i,t represents the counterfactual...`) get rendered as italic instead of body. | The largest-font span heuristic still picks a small span when the body text is split into many short spans. | Low |
| Author order may differ slightly from publisher order due to bbox y/x sort interleaving with superscripts. | All names present, but order can shuffle by one or two. | Low |
| Author affiliation block has email prepended before the schools list (because in the PDF the email span comes first by bbox y). | Cosmetic. | Low |
| Tables in body text are not specifically handled. | Not a problem for papers where tables live in SI or Extended Data captioned blocks. Could matter for review articles. | Latent risk |

## Approaches already tried and rejected

Don't propose these as fixes; they were investigated and ruled out:

| Attempt | Why rejected |
|---|---|
| `pdftotext -layout` (poppler) | Preserves visual columns → reads each scanline left-then-right, garbling reading order. |
| `pdftotext` with no flag | Column flow OK, but no figures, no headings, hyphenation broken (`eco-\nsystems`). |
| `pymupdf get_text("text", sort=True)` | Same scanline problem as `pdftotext -layout`. |
| `markitdown` (Microsoft v0.1.5) | Two-column flow handled, but zero images extracted (`grep -c '!\[' = 0`), hyphenation not fixed. |
| Raster-only figure detection (`page.get_image_info()` alone) | Nature figures are vector graphics; raster-only detection found 2 of 7 figures. |
| Caption-style detection by font alone | Misclassifies inside-caption regular spans as body. Block-level approach (any line matches caption regex → whole block is caption) was the fix. |
| Same-column-only caption merging | Misses cross-column caption layouts. `y_overlap > 5` merges parallel column halves. |
| Longest-span line classifier | Picks superscript-rich author names by char count. Largest-font span was the fix. |
