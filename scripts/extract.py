"""Extract a double-column PDF (Nature article + InDesign supplementary) to a
clean single-file Markdown with figures placed in reading order.

Key features:
- Detects body (font, size) per document by character count.
- Splits long PDF blocks into separate paragraphs at first-line indents.
- Identifies title / authors / abstract / sections / sub-sections / captions /
  affiliations / footers via font size relative to body and content patterns.
- Merges paragraphs that wrap across columns or pages (mid-sentence + lowercase).
- Merges caption continuations (caption font and body-font wrap-arounds).
- Renders each figure region as a single PNG positioned where its caption is.
"""
import fitz
import re
import os
from collections import Counter


# ---------- Patterns ----------
CAPTION_RE = re.compile(
    r"^(Fig\.|Figure|Extended Data Fig\.|Extended Data Figure|"
    r"Supplementary Fig\.|Supplementary Figure|"
    r"Supplementary Table|Extended Data Table|Table)\s*\d+",
    re.IGNORECASE,
)
AUTHOR_HINT_RE = re.compile(r"&|[A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+(?:\s*\d|\s*[,&])")
AFFIL_HINT_RE = re.compile(
    r"University|Institute|Department|School|Centre|Center|Laborator|Faculty|"
    r"Academy|@|e[\s\-]*mail",
    re.IGNORECASE,
)
DOI_RE = re.compile(r"^https?://doi\.org/")
NOISE_LINE_RES = [
    re.compile(r"^Nature\s+Climate\s+Change\s*\|\s*Volume", re.IGNORECASE),
    re.compile(r"^nature\s+climate\s+change\s*$", re.IGNORECASE),
    re.compile(r"^Article\s*$"),
    re.compile(r"^Check for updates\s*$", re.IGNORECASE),
    re.compile(r"^(Received|Accepted|Published online):", re.IGNORECASE),
    re.compile(r"^\s*\d{1,4}\s*$"),
]

# Small connector words: when a line ends with "-" and the next word is one of
# these, the hyphen is part of a compound modifier that should be preserved.
SHORT_GLUE = {
    "to", "of", "in", "at", "on", "or", "by", "as", "and", "from", "for", "is",
    "based", "year", "term", "specific", "level", "scale", "wide", "intensive",
    "intensity", "policy", "level", "scale", "down", "up", "off", "out", "side",
}


def is_noise_line(text):
    s = text.strip()
    if not s:
        return True
    if DOI_RE.match(s):
        return True
    for r in NOISE_LINE_RES:
        if r.match(s):
            return True
    return False


def fix_hyphenation(text):
    """Join 'differ- ences' -> 'differences'. Preserve compound modifiers like
    'Medium- to high' (kept as 'Medium- to', with the trailing space) by leaving
    them alone when the next word is a short glue word."""
    def repl(m):
        before, after = m.group(1), m.group(2)
        if after.lower() in SHORT_GLUE:
            return f"{before}- {after}"   # keep the space for "Medium- to"
        return f"{before}{after}"
    text = re.sub(r"(\w+)-\s+(\w+)", repl, text)
    # Em-dash should be flush with surrounding words in scientific writing
    text = re.sub(r"\s*—\s*", "—", text)
    # Some URLs get an injected space at line breaks ("doi. org" -> "doi.org")
    text = re.sub(r"(https?://[^\s]*?)\s+([a-zA-Z]{2,5}/)", r"\1\2", text)
    text = re.sub(r"(\.\s)(org|com|net|edu|gov|io|de|uk|cn|eu|fr|jp)/", r".\2/", text)
    return text


def collapse_ws(text):
    return re.sub(r"\s+", " ", text).strip()


# ---------- Document-level body font detection ----------
def determine_body_font(doc):
    counter = Counter()
    for page in doc:
        d = page.get_text("dict")
        for blk in d.get("blocks", []):
            if blk.get("type") != 0:
                continue
            for line in blk["lines"]:
                for span in line["spans"]:
                    size = round(span["size"], 1)
                    if size < 6 or size > 14:
                        continue
                    counter[(size, span["font"])] += len(span["text"])
    if not counter:
        return 8.2, ""
    (size, font), _ = counter.most_common(1)[0]
    return size, font


def is_bold_font(font):
    return "Bold" in font or "bold" in font.lower() or "Semibold" in font


# ---------- Line classification ----------
def classify_line(text, spans, body_size):
    if is_noise_line(text):
        return "noise"
    if CAPTION_RE.match(text.strip()):
        return "caption_start"

    # Use the largest-font span as the primary classifier; superscripts/subscripts
    # are at much smaller sizes and shouldn't dominate the line role. When sizes
    # tie, prefer the span with more characters.
    primary = max(spans, key=lambda s: (round(s["size"], 1), len(s["text"])))
    size = round(primary["size"], 1)
    font = primary["font"]
    bold = is_bold_font(font)

    if size < body_size * 0.72:
        return "tiny"        # superscript reference numbers
    if size >= body_size * 2.0 and bold:
        return "title"
    if size >= body_size * 1.25 and bold:
        return "h2"               # named sections (## level)
    if bold and body_size * 1.0 <= size < body_size * 1.25:
        return "h3_or_caption"    # sub-section OR caption (disambiguate)
    if size >= body_size * 1.15 and not bold:
        return "abstract"
    if size < body_size * 0.95 and bold:
        return "small_caption"    # main paper Fig. caption font
    if size < body_size * 0.95:
        return "small"            # affiliations, footnotes
    return "body"


# ---------- Split a PDF block into paragraphs by first-line indent ----------
def split_block_by_indent(blk):
    """Return list of paragraph segments, each segment = list of line dicts."""
    lines = [l for l in blk["lines"]
             if any(s["text"].strip() for s in l.get("spans", []))]
    if len(lines) <= 1:
        return [lines] if lines else []
    # Establish the un-indented x0 baseline as the most common rounded x0
    x0_rounded = [round(l["bbox"][0]) for l in lines]
    baseline = Counter(x0_rounded).most_common(1)[0][0]
    paragraphs = [[lines[0]]]
    for i in range(1, len(lines)):
        x0 = lines[i]["bbox"][0]
        if x0 > baseline + 4:
            paragraphs.append([lines[i]])
        else:
            paragraphs[-1].append(lines[i])
    return paragraphs


# ---------- Build items from a page ----------
def build_paragraphs(page, body_size, is_first_page=False):
    width = page.rect.width
    height = page.rect.height
    mid_x = width / 2
    margin_top = 25
    margin_bot = height - 25

    d = page.get_text("dict")
    items = []

    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        # If any line in this block matches the caption regex, the entire block
        # is a single caption — caption text typically alternates between bold
        # ("Fig. 1 | ...") and regular spans, so we shouldn't split it on font.
        block_is_caption = False
        for line in blk.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s["text"] for s in spans).strip()
            if text and CAPTION_RE.match(text):
                block_is_caption = True
                break

        if block_is_caption:
            all_lines = []
            bbox_x0 = bbox_y0 = float("inf")
            bbox_x1 = bbox_y1 = float("-inf")
            for line in blk["lines"]:
                spans = line.get("spans", [])
                line_text = "".join(s["text"] for s in spans).rstrip()
                if not line_text.strip():
                    continue
                lb = line["bbox"]
                if lb[1] < margin_top or lb[3] > margin_bot:
                    continue
                all_lines.append(line_text)
                bbox_x0 = min(bbox_x0, lb[0])
                bbox_y0 = min(bbox_y0, lb[1])
                bbox_x1 = max(bbox_x1, lb[2])
                bbox_y1 = max(bbox_y1, lb[3])
            if all_lines:
                text = collapse_ws(fix_hyphenation(" ".join(all_lines)))
                if text:
                    items.append({
                        "role": "caption_start",
                        "text": text,
                        "bbox": (bbox_x0, bbox_y0, bbox_x1, bbox_y1),
                    })
            continue

        # Otherwise split block at indent boundaries -> separate paragraph segments
        for seg_lines in split_block_by_indent(blk):
            runs = []
            for line in seg_lines:
                spans = line.get("spans", [])
                line_text = "".join(s["text"] for s in spans).rstrip()
                if not line_text.strip():
                    continue
                role = classify_line(line_text, spans, body_size)
                line_bbox = line["bbox"]
                if line_bbox[1] < margin_top or line_bbox[3] > margin_bot:
                    if role in ("body", "abstract"):
                        pass
                    else:
                        role = "noise"
                if role in ("noise", "tiny"):
                    continue
                if runs and runs[-1][0] == role:
                    runs[-1][1].append(line_text)
                    rb = runs[-1][2]
                    runs[-1] = (role, runs[-1][1], (
                        min(rb[0], line_bbox[0]),
                        min(rb[1], line_bbox[1]),
                        max(rb[2], line_bbox[2]),
                        max(rb[3], line_bbox[3]),
                    ))
                else:
                    runs.append((role, [line_text], tuple(line_bbox)))

            for role, lines, bbox in runs:
                text = collapse_ws(fix_hyphenation(" ".join(lines)))
                if not text:
                    continue
                items.append({"role": role, "text": text, "bbox": bbox})

    # Heuristic disambiguation
    def looks_like_author(t):
        # at least one personal name pattern, or an "&" between names
        if "&" in t:
            return True
        # Two consecutive capitalized words anywhere in the text (a personal name).
        # Requires the second word to also start with a capital letter to avoid
        # matching "Supplementary information" / regular sentence starts.
        if re.search(r"\b[A-Z][a-zA-Z\.\-]+\s+[A-Z][a-zA-Z\.\-]+\b", t):
            return True
        # Pure superscript fragments like "1,8," are author-row digits
        if re.match(r"^[\d,\s]+$", t.strip()):
            return True
        return False

    for it in items:
        if it["role"] == "h3_or_caption":
            if is_first_page and looks_like_author(it["text"]):
                it["role"] = "author"
            elif len(it["text"]) > 120:
                it["role"] = "body"
            else:
                it["role"] = "h3"
        if it["role"] == "small" and AFFIL_HINT_RE.search(it["text"]):
            it["role"] = "affiliation"
        if is_first_page and it["role"] == "small_caption" and looks_like_author(it["text"]):
            it["role"] = "author"

    # Column classification
    for it in items:
        x0, y0, x1, y1 = it["bbox"]
        bw = x1 - x0
        if bw > width * 0.55:
            it["col"] = "full"
        elif x1 < mid_x + 30:
            it["col"] = "left"
        elif x0 > mid_x - 30:
            it["col"] = "right"
        else:
            it["col"] = "full"

    return items


# ---------- Caption continuation merging ----------
def merge_caption_continuations(items):
    """Adjacent caption-style items merge into one caption.

    Triggers:
    - Two caption_start items at similar y range (multi-column caption layout).
    - small_caption / small items right after a caption.
    - body items that vertically continue a caption mid-sentence (lowercase).
    """
    out = []
    for it in items:
        if out and out[-1]["role"] in ("caption_start", "caption"):
            prev_bbox = out[-1]["bbox"]
            cur_bbox = it["bbox"]
            v_gap = cur_bbox[1] - prev_bbox[3]
            y_overlap = (
                min(prev_bbox[3], cur_bbox[3]) - max(prev_bbox[1], cur_bbox[1])
            )
            same_col = out[-1].get("col") == it.get("col")
            should_merge = False

            # Caption split across columns at similar y range
            if it["role"] == "caption_start" and y_overlap > 5:
                should_merge = True
            # Small/caption text right after caption — either vertically (same
            # column) or horizontally (parallel columns at the same y range).
            # Skip very short fragments that are usually figure-internal labels.
            elif it["role"] in ("small_caption", "small"):
                if len(it["text"]) < 20:
                    pass  # too short to be caption continuation
                elif (same_col and -5 <= v_gap < 60) or y_overlap > 5:
                    should_merge = True
            # Body wrap-around continuing caption sentence
            elif it["role"] == "body" and -5 <= v_gap < 60 and same_col:
                prev_text = out[-1]["text"]
                cur_text = it["text"]
                ends_open = bool(prev_text and prev_text[-1] not in ".!?:;)\"”]")
                starts_lower = bool(cur_text and cur_text[0].islower())
                if ends_open and starts_lower:
                    should_merge = True

            if should_merge:
                merged = out[-1]["text"] + " " + it["text"]
                out[-1]["text"] = collapse_ws(fix_hyphenation(merged))
                out[-1]["bbox"] = (
                    min(prev_bbox[0], cur_bbox[0]),
                    min(prev_bbox[1], cur_bbox[1]),
                    max(prev_bbox[2], cur_bbox[2]),
                    max(prev_bbox[3], cur_bbox[3]),
                )
                out[-1]["role"] = "caption"
                continue
        if it["role"] == "caption_start":
            it = dict(it)
            it["role"] = "caption"
        out.append(it)
    return out


# ---------- Figure region detection and rendering ----------
def detect_and_render_figures(page, items, fig_dir, fig_prefix, page_num,
                              fig_counter_start, render_dpi=180):
    width = page.rect.width
    height = page.rect.height

    captions = [it for it in items if it["role"] == "caption"]
    captions.sort(key=lambda c: c["bbox"][1])

    graphics_bboxes = []
    for im in page.get_image_info():
        bb = im["bbox"]
        if (bb[2] - bb[0]) * (bb[3] - bb[1]) > 100:
            graphics_bboxes.append(bb)
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        if (r.x1 - r.x0) * (r.y1 - r.y0) > 50:
            graphics_bboxes.append((r.x0, r.y0, r.x1, r.y1))

    fig_counter = fig_counter_start

    for idx, cap in enumerate(captions):
        cap_y0 = cap["bbox"][1]
        if idx > 0:
            search_y_min = captions[idx - 1]["bbox"][3] + 2
        else:
            search_y_min = 30

        in_range = [bb for bb in graphics_bboxes
                    if bb[1] >= search_y_min - 1 and bb[3] <= cap_y0 + 1]
        if not in_range:
            continue

        gx0 = min(b[0] for b in in_range)
        gy0 = min(b[1] for b in in_range)
        gx1 = max(b[2] for b in in_range)
        gy1 = max(b[3] for b in in_range)

        x0, y0, x1, y1 = gx0, gy0, gx1, gy1
        for _ in range(6):
            changed = False
            for it in items:
                if it.get("consumed") or it["role"] == "caption":
                    continue
                tx0, ty0, tx1, ty1 = it["bbox"]
                if not (search_y_min - 2 <= ty0 and ty1 <= cap_y0 + 1):
                    continue
                if (ty1 - ty0) > 50 or len(it["text"]) > 250:
                    continue
                near_v = (ty0 <= y1 + 60 and ty1 >= y0 - 60)
                near_h = (tx0 <= x1 + 30 and tx1 >= x0 - 30)
                if near_v and near_h:
                    it["pending_consume"] = True
                    x0 = min(x0, tx0)
                    y0 = min(y0, ty0)
                    x1 = max(x1, tx1)
                    y1 = max(y1, ty1)
                    changed = True
            if not changed:
                break

        x0 = max(0, x0 - 4)
        y0 = max(0, y0 - 4)
        x1 = min(width, x1 + 4)
        y1 = min(height, y1 + 4)

        if (y1 - y0) < 30 or (x1 - x0) < 30:
            continue

        scale = render_dpi / 72.0
        clip = fitz.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)

        fig_counter += 1
        fig_name = f"{fig_prefix}_p{page_num+1:03d}_f{fig_counter:02d}.png"
        fig_path = os.path.join(fig_dir, fig_name)
        pix.save(fig_path)

        cap["figure_path"] = fig_path
        cap["figure_name"] = fig_name

        for it in items:
            if it.get("pending_consume"):
                it["consumed"] = True
                it.pop("pending_consume", None)
                continue
            tx0, ty0, tx1, ty1 = it["bbox"]
            if (it["role"] != "caption"
                    and tx0 >= x0 - 2 and tx1 <= x1 + 2
                    and ty0 >= y0 - 2 and ty1 <= y1 + 2):
                it["consumed"] = True

    return [it for it in items if not it.get("consumed")], fig_counter


# ---------- Page-level ordering ----------
def order_items(items):
    full = sorted([i for i in items if i["col"] == "full"], key=lambda i: i["bbox"][1])
    left = sorted([i for i in items if i["col"] == "left"], key=lambda i: i["bbox"][1])
    right = sorted([i for i in items if i["col"] == "right"], key=lambda i: i["bbox"][1])

    if left or right:
        first_col_y = min(i["bbox"][1] for i in left + right)
        last_col_y = max(i["bbox"][3] for i in left + right)
        full_top = [i for i in full if i["bbox"][3] <= first_col_y + 5]
        full_bot = [i for i in full if i["bbox"][1] >= last_col_y - 5]
        full_mid = [i for i in full if i not in full_top and i not in full_bot]
    else:
        full_top, full_mid, full_bot = full, [], []

    return full_top + left + right + full_mid + full_bot


def render_equations_as_images(page, items, fig_dir, fig_prefix, page_num,
                                eq_counter_start, render_dpi=180):
    """Cluster consecutive same-column 'small'/'small_caption' items that are
    vertically adjacent and render them as a single image (math equations)."""
    width = page.rect.width
    height = page.rect.height
    eq_counter = eq_counter_start

    # Group items: equation parts are often side-by-side on the same line. Build
    # clusters by checking each new item against every existing item: include it
    # in a cluster if their bboxes are within tolerance horizontally AND vertically.
    smalls = sorted(
        [it for it in items if it["role"] in ("small", "small_caption")
         and not it.get("consumed")],
        key=lambda i: (i.get("col", ""), i["bbox"][1], i["bbox"][0]),
    )

    def close_enough(a, b):
        if a.get("col") != b.get("col"):
            return False
        ax0, ay0, ax1, ay1 = a["bbox"]
        bx0, by0, bx1, by1 = b["bbox"]
        y_dist = max(0, by0 - ay1, ay0 - by1)
        x_dist = max(0, bx0 - ax1, ax0 - bx1)
        # Same horizontal line, or one stacked just above/below
        return y_dist < 18 and x_dist < 220

    clusters = []
    for it in smalls:
        added = False
        for cluster in reversed(clusters):
            if any(close_enough(it, m) for m in cluster):
                cluster.append(it)
                added = True
                break
        if not added:
            clusters.append([it])
    clusters = [c for c in clusters if len(c) >= 2]

    for cluster in clusters:
        x0 = min(c["bbox"][0] for c in cluster) - 4
        y0 = min(c["bbox"][1] for c in cluster) - 4
        x1 = max(c["bbox"][2] for c in cluster) + 4
        y1 = max(c["bbox"][3] for c in cluster) + 4
        x0 = max(0, x0); y0 = max(0, y0)
        x1 = min(width, x1); y1 = min(height, y1)
        # Heuristic: equations are relatively narrow (less than full page width)
        # and span multiple short text fragments
        scale = render_dpi / 72.0
        clip = fitz.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)

        eq_counter += 1
        eq_name = f"{fig_prefix}_p{page_num+1:03d}_eq{eq_counter:02d}.png"
        eq_path = os.path.join(fig_dir, eq_name)
        pix.save(eq_path)

        # Replace the first item with an equation image item, mark rest consumed
        first = cluster[0]
        first["role"] = "equation"
        first["text"] = ""
        first["figure_path"] = eq_path
        first["figure_name"] = eq_name
        first["bbox"] = (x0, y0, x1, y1)
        for c in cluster[1:]:
            c["consumed"] = True

    return [it for it in items if not it.get("consumed")], eq_counter


# ---------- Cross-block paragraph wrap merging ----------
def merge_wrapped_bodies(items):
    """Merge wrapped paragraphs across columns/pages. When the previous body
    block ends mid-sentence and the current body block starts lowercase, glue
    them together. Items in the gap that are not real content disruptors
    (authors, affiliations, free-floating page-1 metadata) are ignored."""
    # Only real section breaks should prevent body merging. Page-1 front matter
    # (title/author/abstract/affiliation) is interleaved by bbox order but is
    # not part of the body flow. Captions also don't break body flow because
    # body text often wraps around figures.
    DISRUPTIVE = {"h2", "h3"}
    out = []
    for it in items:
        if it["role"] == "body" and out:
            # Find the most recent body item without a disruptive item between
            disruptive_seen = False
            prev_body_idx = None
            for j in range(len(out) - 1, -1, -1):
                role = out[j]["role"]
                if role in DISRUPTIVE:
                    disruptive_seen = True
                    break
                if role == "body":
                    prev_body_idx = j
                    break
            if prev_body_idx is not None and not disruptive_seen:
                prev = out[prev_body_idx]
                ends_open = bool(prev["text"] and prev["text"][-1] not in ".!?:;)\"”]")
                first_ch = it["text"][0] if it["text"] else ""
                starts_continuation = (
                    first_ch.islower() or first_ch in "([{“‘"
                )
                if ends_open and starts_continuation:
                    prev["text"] = collapse_ws(
                        fix_hyphenation(prev["text"] + " " + it["text"])
                    )
                    continue
        out.append(it)
    return out


# ---------- Markdown rendering ----------
def render_markdown(items_by_page, fig_dir_rel="figures"):
    md = []

    page1 = items_by_page[0] if items_by_page else []
    title_items = [it for it in page1 if it["role"] == "title"]
    author_items = [it for it in page1 if it["role"] == "author"]
    abstract_items = [it for it in page1 if it["role"] == "abstract"]
    affil_items = [it for it in page1 if it["role"] == "affiliation"]

    if title_items:
        md.append(f"# {collapse_ws(' '.join(it['text'] for it in title_items))}")
        md.append("")
    if author_items:
        # The PDF interleaves author names with their numeric superscripts in
        # separate spans, so when we order by bbox the digits scatter through
        # the author list. Strip standalone digit groups and the authors render
        # cleanly as a readable list.
        raw = " ".join(it["text"] for it in author_items)
        cleaned = re.sub(r"\s*\b\d+(?:,\d+)*\b", "", raw)
        cleaned = re.sub(r"\s+,", ",", cleaned)
        cleaned = re.sub(r",\s*&", " &", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
        md.append("**Authors:** " + cleaned)
        md.append("")
    if affil_items:
        md.append("**Affiliations:** " + collapse_ws(" ".join(it["text"] for it in affil_items)))
        md.append("")
    if abstract_items:
        md.append("## Abstract")
        md.append("")
        for it in abstract_items:
            md.append(it["text"])
            md.append("")

    # Only emit a "## Main" wrapper when there's an abstract above (matches the
    # Nature article HTML convention). For supplementary documents without an
    # abstract, body text just starts directly under the title.
    main_needed = bool(abstract_items)
    main_emitted = False
    for pi, items in enumerate(items_by_page):
        for it in items:
            if pi == 0 and it["role"] in ("title", "author", "abstract", "affiliation"):
                continue
            r = it["role"]
            t = it["text"]

            if r == "body" and main_needed and not main_emitted:
                md.append("## Main")
                md.append("")
                main_emitted = True

            if r == "title":
                md.append(f"# {t}")
                md.append("")
            elif r == "h2":
                md.append("")
                md.append(f"## {t}")
                md.append("")
            elif r == "h3":
                md.append("")
                md.append(f"### {t}")
                md.append("")
            elif r == "abstract":
                md.append(t)
                md.append("")
            elif r == "caption":
                fig_name = it.get("figure_name")
                if fig_name:
                    rel = os.path.join(fig_dir_rel, fig_name)
                    stem = os.path.splitext(fig_name)[0]
                    md.append("")
                    md.append(f"![{stem}]({rel})")
                    md.append("")
                md.append(f"*{t}*")
                md.append("")
            elif r == "equation":
                fig_name = it.get("figure_name")
                rel = os.path.join(fig_dir_rel, fig_name)
                stem = os.path.splitext(fig_name)[0]
                md.append("")
                md.append(f"![{stem}]({rel})")
                md.append("")
            elif r in ("small", "small_caption", "affiliation"):
                # Drop orphan single-character math fragments like "*ˆ*"
                if len(t) <= 2:
                    continue
                md.append(f"*{t}*")
                md.append("")
            elif r == "author":
                md.append(f"**Authors:** {t}")
                md.append("")
            else:
                # If this looks like a packed numbered reference list ("1. Foo... 2. Bar..."),
                # split it onto separate lines for readability.
                if (r == "body"
                        and t.count(". ") > 5
                        and re.search(r"\b\d{1,3}\.\s+[A-Z]", t)):
                    pieces = re.split(r"(?<=\.\s)(?=\d{1,3}\.\s+[A-Z])", t)
                    if len(pieces) > 3:
                        for p in pieces:
                            p = p.strip()
                            if p:
                                md.append(p)
                                md.append("")
                        continue
                md.append(t)
                md.append("")

    text = "\n".join(md)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text


# ---------- Driver ----------
def extract_to_markdown(pdf_path, out_md_path, fig_dir, fig_prefix, render_dpi=180):
    doc = fitz.open(pdf_path)
    os.makedirs(fig_dir, exist_ok=True)

    body_size, body_font = determine_body_font(doc)
    print(f"  [{fig_prefix}] body: size={body_size} font={body_font}")

    items_by_page = []
    fig_counter = 0
    eq_counter = 0
    fig_dir_rel = os.path.relpath(fig_dir, os.path.dirname(out_md_path))

    for page_num, page in enumerate(doc):
        items = build_paragraphs(page, body_size, is_first_page=(page_num == 0))
        items = merge_caption_continuations(items)
        items, fig_counter = detect_and_render_figures(
            page, items, fig_dir, fig_prefix, page_num, fig_counter,
            render_dpi=render_dpi,
        )
        items, eq_counter = render_equations_as_images(
            page, items, fig_dir, fig_prefix, page_num, eq_counter,
            render_dpi=render_dpi,
        )
        items = order_items(items)
        items_by_page.append(items)

    # Merge wrapped paragraphs across page boundaries by flattening then re-splitting
    flat = [it for page_items in items_by_page for it in page_items]
    flat = merge_wrapped_bodies(flat)
    # Rebuild items_by_page based on the flat list (page identity isn't important
    # downstream since the renderer concatenates pages anyway)
    items_by_page = [flat]

    md = render_markdown(items_by_page, fig_dir_rel=fig_dir_rel)

    with open(out_md_path, "w") as f:
        f.write(md)

    return len(doc), fig_counter


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=(
            "Convert academic-journal PDFs (Nature, Science, journal articles, "
            "supplementary information) to clean single-file Markdown with figures "
            "and equations rendered as PNGs in reading order."
        )
    )
    parser.add_argument("pdfs", nargs="+", help="One or more PDF files to convert.")
    parser.add_argument(
        "-o", "--output",
        help="Output markdown path. Only valid in single-PDF mode. "
             "Default: <pdf-basename>.md next to the PDF.",
    )
    parser.add_argument(
        "--dpi", type=int, default=180,
        help="Render DPI for figure/equation PNGs (default: 180; try 240 for print-quality).",
    )
    parser.add_argument(
        "--prefix",
        help="Figure filename prefix (figures named <prefix>_p<page>_f<n>.png). "
             "Default: derived from PDF basename.",
    )
    parser.add_argument(
        "--figures-dir",
        help="Directory for figure/equation PNGs. "
             "Default: `figures/` next to the first PDF.",
    )
    args = parser.parse_args()

    if args.output and len(args.pdfs) > 1:
        sys.exit("error: -o/--output is only valid when converting a single PDF.")

    first_dir = os.path.dirname(os.path.abspath(args.pdfs[0]))
    fig_dir = args.figures_dir or os.path.join(first_dir, "figures")

    def derive_prefix(pdf_path):
        if args.prefix:
            return args.prefix
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        # SI / supplement → "SI"; otherwise sanitized basename, truncated for sanity.
        if re.match(r"^(SI|supp|supplement)", base, re.IGNORECASE):
            return "SI"
        sane = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")
        return (sane[:24] if sane else "doc")

    for pdf_path in args.pdfs:
        if args.output:
            out_md = args.output
        else:
            pdf_dir = os.path.dirname(os.path.abspath(pdf_path))
            base = os.path.splitext(os.path.basename(pdf_path))[0]
            out_md = os.path.join(pdf_dir, base + ".md")

        prefix = derive_prefix(pdf_path)
        pages, figs = extract_to_markdown(
            pdf_path, out_md, fig_dir, prefix, render_dpi=args.dpi,
        )
        print(f"  → {out_md}: {pages} pages, {figs} figures")
