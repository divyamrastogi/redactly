"""
Generic UK bank-statement transaction redaction (BETA).

Mirrors ``redact_barclaycard.py``'s span-grouping approach but is layout-driven
rather than hard-coded: column roles (Date / Description / Paid out / Paid in /
Balance) are discovered from the statement's own column-header row by clustering
the header spans on the X axis, so it works across bank layouts instead of one
fixed geometry.

Layout model (all discovered, never assumed):
  - A header row containing labels such as Date, Description, Paid out, Paid in,
    Balance defines the X position of each column. Column bands are the gaps
    between consecutive header centres (split at the midpoints).
  - Transaction rows start with a date span; every other span on that baseline
    inherits the role of whichever column band its X-centre falls in.
  - Whitelist: keep a row if any keyword (case-insensitive substring) matches its
    description; otherwise add true-redaction rects over the date, description,
    paid-in and paid-out cells. The Balance column is NEVER redacted, and page
    furniture above the header (sort code, name, etc.) is left untouched.

Return contract matches ``redact_barclaycard``:
    (output_path, total_of_kept_amounts, kept_rows)

Usage:
  python3 redact_bank_generic.py <input.pdf> <output.pdf> <keyword1> [keyword2 ...]
"""

import fitz
import re
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ── Patterns ────────────────────────────────────────────────────────────────
# Row dates: "1 May" / "01 May" (D MMM), "01/05/2026" (DD/MM/YYYY), "01/05/26"
DATE_PATTERNS = [
    re.compile(r'^\d{1,2}\s+[A-Za-z]{3}$'),
    re.compile(r'^\d{1,2}/\d{1,2}/\d{4}$'),
    re.compile(r'^\d{1,2}/\d{1,2}/\d{2}$'),
]

# Amounts: "1,234.56", "1234.56", "£1,234.56", "1,234.56CR", "-12.34"
AMOUNT_RE = re.compile(r'^-?£?\s?[\d,]+\.\d{2}\s*(?:CR)?$', re.IGNORECASE)

# Column-header labels → role. Order matters: checked top-to-bottom, first match
# wins. "balance" is checked first so a header like "Running balance" still maps
# to balance. Used ONLY to locate the header line; data-span roles come from the
# column bands, so a description such as "Balance transfer" cannot mislead us.
ROLE_LABELS = [
    ('balance',     ['balance']),
    ('paid_out',    ['paid out', 'money out', 'withdrawal', 'debit', 'payment out', 'payments out']),
    ('paid_in',     ['paid in', 'money in', 'receipt', 'deposit', 'credit']),
    ('date',        ['date']),
    ('description', ['description', 'details', 'narrative', 'transaction', 'payee', 'memo']),
]

# Tolerance (points) for grouping spans onto the same text baseline.
ROW_Y_TOLERANCE = 3


# ── Header / column detection ───────────────────────────────────────────────
def _header_role(text):
    """Map a column-header label to a role, or None if it isn't a header label."""
    t = text.strip().lower()
    if not t:
        return None
    for role, labels in ROLE_LABELS:
        for lbl in labels:
            if lbl in t:
                return role
    return None


def _detect_columns(spans, page_width):
    """Find the header line and derive column bands by X-clustering the headers.

    Returns (columns, header_y) where columns is a list of
    ``{'role', 'x0', 'x1'}`` sorted left-to-right, or (None, None) if no header
    line could be identified (in which case we refuse to redact blindly).
    """
    # Candidate header spans (those whose label maps to a known role).
    candidates = []
    for s in spans:
        role = _header_role(s['text'])
        if role:
            candidates.append((s, role))
    if not candidates:
        return None, None

    # Cluster candidates by Y to find the dominant header line. A real header
    # line has several labelled spans on one baseline; stray description text
    # that happens to contain a label ("Balance transfer") forms a 1-span
    # cluster and never dominates.
    candidates.sort(key=lambda t: t[0]['bbox'][1])
    clusters = []
    for span, role in candidates:
        y = span['bbox'][1]
        placed = False
        for c in clusters:
            if abs(c['y'] - y) <= 5:
                c['items'].append((span, role))
                placed = True
                break
        if not placed:
            clusters.append({'y': y, 'items': [(span, role)]})
    clusters.sort(key=lambda c: (-len(c['items']), c['y']))
    header_line = clusters[0]['items']
    header_y = min(sp['bbox'][3] for sp, _ in header_line)  # bottom of header text

    # Sort header spans by X-centre and split the page into bands at the
    # midpoints between consecutive header centres.
    centres = sorted(
        (((sp['bbox'][0] + sp['bbox'][2]) / 2.0, role) for sp, role in header_line),
        key=lambda t: t[0],
    )
    xs = [c for c, _ in centres]
    roles = [r for _, r in centres]
    bounds = [0.0]
    for i in range(len(xs) - 1):
        bounds.append((xs[i] + xs[i + 1]) / 2.0)
    bounds.append(float(page_width))

    columns = [{'role': roles[i], 'x0': bounds[i], 'x1': bounds[i + 1]}
               for i in range(len(roles))]
    logger.info(f"Detected {len(columns)} columns: {[(c['role'], round(c['x0']), round(c['x1'])) for c in columns]}")
    return columns, header_y


def _assign_role(span, columns):
    """Role of the column band the span's X-centre falls into."""
    cx = (span['bbox'][0] + span['bbox'][2]) / 2.0
    for col in columns:  # columns are sorted left-to-right
        if col['x0'] <= cx < col['x1']:
            return col['role']
    # Fall back to the nearest edge column.
    return columns[0]['role'] if cx < columns[0]['x0'] else columns[-1]['role']


# ── Span helpers ────────────────────────────────────────────────────────────
def _collect_spans(page):
    spans = []
    for block in page.get_text("dict")["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                t = span["text"].strip()
                if t:
                    spans.append({"text": t, "bbox": span["bbox"]})
    return spans


def _cluster_rows(spans, tol=ROW_Y_TOLERANCE):
    """Group spans into rows by baseline Y (tolerance 3pt), top-to-bottom."""
    rows = []
    for s in sorted(spans, key=lambda s: (s["bbox"][1], s["bbox"][0])):
        y = s["bbox"][1]
        for r in rows:
            if abs(r["y0"] - y) <= tol:
                r["spans"].append(s)
                break
        else:
            rows.append({"y0": y, "spans": [s]})
    return rows


def _matches_date(text):
    return any(p.match(text.strip()) for p in DATE_PATTERNS)


def _is_amount(text):
    return bool(AMOUNT_RE.match(text.strip()))


def _parse_amount(text):
    s = (text.strip()
         .replace('£', '').replace(',', '')
         .upper().replace('CR', '').strip())
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def _pad_rect(bbox, pad=1.0):
    x0, y0, x1, y1 = bbox
    return fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad)


def _is_date_cell(cell, date_column_known):
    """A cell counts as the row's date if it matches a date pattern and sits in
    the date column (or anywhere, when no Date header was found)."""
    if not _matches_date(cell["text"]):
        return False
    return (not date_column_known) or cell["role"] == "date"


def _row_amount(cells):
    """Best-effort single amount for a kept row: prefer paid-out, else paid-in."""
    for role in ("paid_out", "paid_in"):
        for c in cells:
            if c["role"] == role and _is_amount(c["text"]):
                return _parse_amount(c["text"])
    return 0.0


def _is_continuation(cells):
    """A non-dated row is a wrapped-description continuation of the row above if
    it has description text but no balance or amount cell (which would mark it as
    a closing-balance / totals / furniture line that must be left alone)."""
    has_desc = any(c["role"] == "description" for c in cells)
    has_balance = any(c["role"] == "balance" for c in cells)
    has_amount = any(c["role"] in ("paid_in", "paid_out") and _is_amount(c["text"]) for c in cells)
    return has_desc and not has_balance and not has_amount


# ── Per-page redaction ──────────────────────────────────────────────────────
def _redact_page(page, keep_keywords, kept_rows):
    spans = _collect_spans(page)
    columns, header_y = _detect_columns(spans, page.rect.width)
    if not columns:
        logger.warning(f"No column header line detected on page — leaving it untouched.")
        return

    date_column_known = any(c["role"] == "date" for c in columns)
    rows = _cluster_rows(spans)

    current_redacted = False  # was the most recent transaction redacted?
    for row in rows:
        if row["y0"] <= header_y:
            continue  # header line or page furniture above it (name, sort code)
        cells = [dict(s, role=_assign_role(s, columns)) for s in row["spans"]]

        date_cells = [c for c in cells if _is_date_cell(c, date_column_known)]
        if date_cells:
            description = " ".join(c["text"] for c in cells if c["role"] == "description").strip()
            kept = any(kw.lower() in description.lower() for kw in keep_keywords) if keep_keywords else False
            amount = _row_amount(cells)
            if kept:
                current_redacted = False
                kept_rows.append({"description": description, "amount": amount})
                logger.info(f"  KEEP   {date_cells[0]['text']} | {description} | £{amount:.2f}")
            else:
                current_redacted = True
                logger.info(f"  REDACT {date_cells[0]['text']} | {description}")
                # Redact every cell except the balance column (and unknowns).
                for c in cells:
                    if c["role"] in ("date", "description", "paid_in", "paid_out"):
                        page.add_redact_annot(_pad_rect(c["bbox"]), fill=(0, 0, 0))
        else:
            # Wrapped description belonging to the transaction above.
            if current_redacted and _is_continuation(cells):
                for c in cells:
                    if c["role"] == "description":
                        page.add_redact_annot(_pad_rect(c["bbox"]), fill=(0, 0, 0))
            # Rows with a balance/amount cell but no date (closing balance,
            # carried-forward totals) and pure furniture are left untouched.

    page.apply_redactions()


# ── Public entry point ──────────────────────────────────────────────────────
def redact_bank_generic(input_path, output_path, keep_keywords):
    """Redact every transaction not matching ``keep_keywords`` from a generic UK
    bank statement. Returns (output_path, total_of_kept_amounts, kept_rows)."""
    doc = fitz.open(input_path)
    kept_rows = []

    for page_num in range(len(doc)):
        _redact_page(doc[page_num], keep_keywords, kept_rows)

    total = sum(r.get("amount", 0.0) for r in kept_rows)
    doc.save(output_path)
    doc.close()

    logger.info(f"\nKept {len(kept_rows)} transactions totalling £{total:.2f}")
    logger.info(f"Saved: {output_path}")
    return output_path, total, kept_rows


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    input_pdf = sys.argv[1]
    output_pdf = sys.argv[2]
    keywords = sys.argv[3:]

    out, total, kept = redact_bank_generic(input_pdf, output_pdf, keywords)

    base = os.path.splitext(output_pdf)[0]
    final_out = f"{base}_£{total:.2f}.pdf"
    os.rename(out, final_out)

    print(f"\nDone. Kept {len(kept)} transactions = £{total:.2f}")
    print(f"Output: {final_out}")
