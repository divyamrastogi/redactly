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
from collections import Counter

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ── Patterns ────────────────────────────────────────────────────────────────
# Row dates: "1 May" / "01 May" (D MMM), "01/05/2026" (DD/MM/YYYY), "01/05/26"
DATE_PATTERNS = [
    # "1 May" / "01 Jun 26" (HSBC) / "1 May 2026" (Revolut)
    re.compile(r'^\d{1,2}\s+[A-Za-z]{3}(?:\s+\d{2}|\s+\d{4})?$'),
    re.compile(r'^\d{1,2}/\d{1,2}/\d{4}$'),
    re.compile(r'^\d{1,2}/\d{1,2}/\d{2}$'),
]

# Rows that must always survive regardless of keywords (statement structure,
# not transactions): HSBC-style balance carry rows.
ALWAYS_KEEP_RE = re.compile(r'balance\s+(brought|carried)\s+forward', re.IGNORECASE)

# Amounts: "1,234.56", "1234.56", "£1,234.56", "1,234.56CR", "-12.34"
AMOUNT_RE = re.compile(r'^-?£?\s?[\d,]+\.\d{2}\s*(?:CR)?$', re.IGNORECASE)

# Column-header labels → role. Order matters: checked top-to-bottom, first match
# wins. "balance" is checked first so a header like "Running balance" still maps
# to balance. Used ONLY to locate the header line; data-span roles come from the
# column bands, so a description such as "Balance transfer" cannot mislead us.
ROLE_LABELS = [
    ('balance',     ['balance']),
    ('paid_out',    ['paid out', 'money out', 'withdrawal', 'debit', 'payment out', 'payments out', 'outgoing']),
    ('paid_in',     ['paid in', 'money in', 'receipt', 'deposit', 'credit', 'incoming']),
    ('date',        ['date']),
    ('description', ['description', 'details', 'narrative', 'transaction', 'payee', 'memo']),
    # 'Amount' is ambiguous: Wise labels its running-balance column "Amount",
    # while single-money-column banks use it for the transaction amount.
    # _detect_columns resolves it structurally after the bands are built.
    ('amount',      ['amount']),
]

# Tolerance (points) for grouping spans onto the same text baseline.
ROW_Y_TOLERANCE = 3


# ── Header / column detection ───────────────────────────────────────────────
# Longer labels that only ever match EXACTLY (never by containment — a prose
# sentence mentioning these words must not qualify).
EXACT_LABELS = {
    'payment type and details': 'description',   # HSBC
}


def _header_role(text):
    """Map a column-header label to a role, or None if it isn't a header label.

    Header labels are short ("Paid out", "Running balance") — prose that merely
    mentions a label word ("Notes about your balance…") must never qualify, so
    long/wordy spans are rejected and labels match on word boundaries only.
    Known multi-word labels (EXACT_LABELS) match by exact equality instead.
    """
    t = ' '.join(text.strip().lower().split())
    if not t:
        return None
    # De-spaced exact-equality lookup: renderers split or fuse words arbitrarily
    # ("Paym ent type and details", "£Paidout"), so compare letters only.
    t_ns0 = re.sub(r'[^a-z0-9]', '', t)
    for lbl, role in EXACT_LABELS.items():
        if t_ns0 == re.sub(r'[^a-z0-9]', '', lbl):
            return role
    if len(t) > 25 or len(t.split()) > 3:
        return None
    # Some renderers fuse label glyph runs into one un-spaced span ("£Paidout"),
    # so also accept an EXACT de-spaced match (never containment).
    t_ns = re.sub(r'[^a-z0-9]', '', t)
    for role, labels in ROLE_LABELS:
        for lbl in labels:
            if re.search(rf'\b{re.escape(lbl)}\b', t) or t_ns == lbl.replace(' ', ''):
                return role
    return None


def _merge_line_phrases(line_spans, gap=12):
    """Merge adjacent spans on one baseline into phrases (x-gap <= gap pt).

    Banks fragment header labels into separate spans ("£", "Paid", "out" —
    HSBC); merging reunites them so the label matches. It also strengthens the
    prose defence: inline bold segments of a sentence merge back into one long
    phrase, which the word-count limit in _header_role then rejects.
    """
    phrases = []
    cur = None
    for s in sorted(line_spans, key=lambda s: s['bbox'][0]):
        if cur is not None and s['bbox'][0] - cur['bbox'][2] <= gap:
            cur = {'text': cur['text'] + ' ' + s['text'],
                   'bbox': (cur['bbox'][0],
                            min(cur['bbox'][1], s['bbox'][1]),
                            s['bbox'][2],
                            max(cur['bbox'][3], s['bbox'][3]))}
        else:
            if cur is not None:
                phrases.append(cur)
            cur = {'text': s['text'], 'bbox': tuple(s['bbox'])}
    if cur is not None:
        phrases.append(cur)
    return phrases


def _detect_columns(spans, page_width):
    """Find the header line and derive column bands from its label phrases.

    Returns (columns, header_y) where columns is a list of
    ``{'role', 'x0', 'x1'}`` sorted left-to-right, or (None, None) if no header
    line could be identified (in which case we refuse to redact blindly).

    A credible header line needs >= 3 DISTINCT roles, including a date or
    description column AND at least one money column — prose lines that echo
    label words and 2-label summary tables never qualify.
    """
    best = None
    for line in _cluster_rows(spans, tol=5):
        cands = []
        for phrase in _merge_line_phrases(line['spans']):
            role = _header_role(phrase['text'])
            if role:
                cands.append((phrase, role))
        roles_found = {role for _, role in cands}
        if (len(roles_found) >= 3
                and roles_found & {'date', 'description'}
                and roles_found & {'paid_in', 'paid_out', 'balance', 'amount'}):
            if best is None or len(cands) > len(best):
                best = cands
    if best is None:
        logger.info("No credible transaction header line — leaving page untouched.")
        return None, None

    header_line = best
    header_y = min(p['bbox'][3] for p, _ in header_line)  # bottom of header text

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

    # Resolve ambiguous 'amount' columns structurally: when it is the RIGHTMOST
    # column, no explicit balance column exists, and separate in/out columns do
    # (the Wise shape), it is the running balance and must be preserved.
    # Otherwise it is the transaction-amount column and is treated as paid_out
    # (redactable; sign/CR still drives credit detection).
    role_set = {c['role'] for c in columns}
    for i, col in enumerate(columns):
        if col['role'] == 'amount':
            if (i == len(columns) - 1 and 'balance' not in role_set
                    and role_set & {'paid_in', 'paid_out'}):
                col['role'] = 'balance'
            else:
                col['role'] = 'paid_out'

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


def _pad_rect(bbox, pad_x=1.0):
    """Redaction rect for a span: padded horizontally, INSET vertically.

    PyMuPDF removes every character whose box merely intersects a redaction
    rect, and on tightly-leaded statements consecutive rows' span boxes overlap
    vertically — a full-height rect would destroy KEPT rows adjacent to
    redacted ones. A band through the vertical core of the line still removes
    every character of the target row (their boxes all cross the core) while
    never reaching the neighbours."""
    x0, y0, x1, y1 = bbox
    inset = (y1 - y0) * 0.25
    return fitz.Rect(x0 - pad_x, y0 + inset, x1 + pad_x, y1 - inset)


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


def _amount_cells(cells):
    """All paid-in / paid-out cells whose text is a recognised amount."""
    return [c for c in cells
            if c["role"] in ("paid_in", "paid_out") and _is_amount(c["text"])]


def _is_credit(cells, columns):
    """Is this transaction row a credit (money in)?

    Two-column layout (both a Paid in and a Paid out header present): a credit is
    any amount that lands in the Paid in column. Single amount column (or none):
    a credit carries a CR suffix or a leading minus sign — mirroring the existing
    CR handling in ``redact_barclaycard`` (commit 9408e1f).
    """
    has_paid_in = any(c["role"] == "paid_in" for c in columns)
    has_paid_out = any(c["role"] == "paid_out" for c in columns)
    if has_paid_in and has_paid_out:
        return any(c["role"] == "paid_in" for c in _amount_cells(cells))
    for c in _amount_cells(cells):
        t = c["text"].strip()
        if t.upper().endswith("CR") or t.startswith("-"):
            return True
    return False


def _is_continuation(cells):
    """A non-dated row is a wrapped-description continuation of the row above if
    it has description text but no balance or amount cell (which would mark it as
    a closing-balance / totals / furniture line that must be left alone)."""
    has_desc = any(c["role"] == "description" for c in cells)
    has_balance = any(c["role"] == "balance" for c in cells)
    has_amount = any(c["role"] in ("paid_in", "paid_out") and _is_amount(c["text"]) for c in cells)
    return has_desc and not has_balance and not has_amount


# ── Per-page redaction ──────────────────────────────────────────────────────
def _redact_balance_amounts(page, rows, columns, header_y):
    """Financial-summary redaction: remove amount-shaped spans that reveal the
    account's balances rather than the kept transactions.

    Two placements are covered on every transaction page:
    - ABOVE the header: the summary block (opening/closing balance, totals —
      Revolut prints these in the account-details area). Only amount-shaped
      text is touched, so the name/sort-code furniture there is unaffected.
    - BELOW the header, balance column: the running balance is redacted on
      EVERY row — kept ones included. A kept row must prove its own amount
      (paid in / paid out), never the account's running total.
    """
    for row in rows:
        for s in row["spans"]:
            if not _is_amount(s["text"]):
                continue
            if row["y0"] <= header_y or _assign_role(s, columns) == "balance":
                page.add_redact_annot(_pad_rect(s["bbox"]), fill=(0, 0, 0))


def _redact_dateless(page, rows, columns, header_y, keep_keywords, keep_credits, kept_rows):
    """Redact a dateless (Wise-style) transaction table.

    A transaction GROUP is a row carrying at least one amount in a money column
    (the head), plus any immediately-following rows without money cells that sit
    within TAIL_GAP points of the previous group row — Wise puts the date and
    transaction reference on such a tail line. Prose further down the page
    (regulatory boilerplate) is detached by the gap rule and never touched.
    """
    TAIL_GAP = 20  # pt: max vertical gap for a tail line to belong to the group

    groups, current = [], None
    for row in rows:
        if row["y0"] <= header_y:
            continue
        cells = [dict(s, role=_assign_role(s, columns)) for s in row["spans"]]
        has_money = any(c["role"] in ("paid_in", "paid_out") and _is_amount(c["text"])
                        for c in cells)
        if has_money:
            current = {"cells": list(cells), "head": cells, "last_y": row["y0"]}
            groups.append(current)
        elif current is not None and row["y0"] - current["last_y"] <= TAIL_GAP:
            current["cells"].extend(cells)
            current["last_y"] = row["y0"]
        else:
            current = None  # detached row: furniture/boilerplate — untouched

    for g in groups:
        description = " ".join(
            c["text"] for c in g["cells"] if c["role"] == "description").strip()
        is_kw = any(kw.lower() in description.lower()
                    for kw in keep_keywords) if keep_keywords else False
        is_credit = _is_credit(g["head"], columns) if keep_credits else False
        amount = _row_amount(g["head"])
        if is_kw or is_credit:
            kept_rows.append({"description": description, "amount": amount})
            logger.info(f"  KEEP   {description} | £{amount:.2f}")
        else:
            logger.info(f"  REDACT {description}")
            for c in g["cells"]:
                if c["role"] in ("date", "description", "paid_in", "paid_out"):
                    page.add_redact_annot(_pad_rect(c["bbox"]), fill=(0, 0, 0))


def _redact_page(page, keep_keywords, kept_rows, keep_credits=False,
                 redact_balances=False):
    """Redact non-keyword transactions on one page.

    Returns the detected transaction ``header_y`` (bottom of the header row),
    or ``None`` when no credible header was found. The header_y is surfaced so
    the optional Presidio PII pass can reuse it instead of calling
    ``_detect_columns`` a second time per page.

    ``redact_balances`` additionally removes financial-summary amounts (the
    block above the header and the running-balance column) — see
    :func:`_redact_balance_amounts`.
    """
    spans = _collect_spans(page)
    columns, header_y = _detect_columns(spans, page.rect.width)
    if not columns:
        logger.warning(f"No column header line detected on page — leaving it untouched.")
        return None

    date_column_known = any(c["role"] == "date" for c in columns)
    rows = _cluster_rows(spans)

    if not date_column_known:
        # Wise-style dateless layout: no Date column — each transaction is a
        # money row, optionally followed by close-by tail lines underneath
        # (date + transaction reference, wrapped description).
        _redact_dateless(page, rows, columns, header_y,
                         keep_keywords, keep_credits, kept_rows)
        if redact_balances:
            _redact_balance_amounts(page, rows, columns, header_y)
        page.apply_redactions()
        return header_y

    # Dated layouts: a transaction GROUP starts at a row with a date cell and
    # absorbs the following dateless rows that sit close underneath (wrapped
    # descriptions — HSBC also puts the amount on such continuation lines).
    TAIL_GAP = 20
    groups, current = [], None
    for row in rows:
        if row["y0"] <= header_y:
            continue  # header line or page furniture above it (name, sort code)
        cells = [dict(s, role=_assign_role(s, columns)) for s in row["spans"]]
        if any(_is_date_cell(c, date_column_known) for c in cells):
            current = {"cells": list(cells), "last_y": row["y0"]}
            groups.append(current)
        elif current is not None and row["y0"] - current["last_y"] <= TAIL_GAP:
            current["cells"].extend(cells)
            current["last_y"] = row["y0"]
        else:
            current = None  # detached row: totals/furniture — untouched

    for g in groups:
        description = " ".join(
            c["text"] for c in g["cells"] if c["role"] == "description").strip()
        if ALWAYS_KEEP_RE.search(description):
            continue  # brought/carried-forward rows are structure, not spend
        is_kw = any(kw.lower() in description.lower()
                    for kw in keep_keywords) if keep_keywords else False
        is_credit = _is_credit(g["cells"], columns) if keep_credits else False
        amount = _row_amount(g["cells"])
        if is_kw or is_credit:
            kept_rows.append({"description": description, "amount": amount})
            logger.info(f"  KEEP   {description} | £{amount:.2f}")
        else:
            logger.info(f"  REDACT {description}")
            for c in g["cells"]:
                if c["role"] in ("date", "description", "paid_in", "paid_out"):
                    page.add_redact_annot(_pad_rect(c["bbox"]), fill=(0, 0, 0))

    if redact_balances:
        _redact_balance_amounts(page, rows, columns, header_y)
    page.apply_redactions()
    return header_y


# ── Public entry point ──────────────────────────────────────────────────────
def _apply_pii_pass(page, page_num, header_y, pii_mode,
                    structured_only=False, landlord=False):
    """Run the optional Presidio PII pass on a page after its transaction
    redaction.

    ``pii_layer`` is imported lazily so the default ``pii_mode="off"`` path
    never pulls in presidio (and we avoid a circular import, since
    ``pii_layer`` itself imports from this module). The summary log carries
    counts by entity type only — never the matched text of any finding.

    ``structured_only`` is set for headerless pages after the transaction pages
    (info/terms pages: PERSON/PHONE hits there are the bank's own contact
    details). ``landlord`` preserves account-ownership PII — name, account
    number, sort code, IBAN — because a landlord statement must prove whose
    account received the rent.
    """
    from pii_layer import (LANDLORD_PRESERVED_TYPES, analyze_page_pii,
                           apply_pii_redactions)
    findings = analyze_page_pii(
        page, header_y=header_y, structured_only=structured_only,
        exclude_types=LANDLORD_PRESERVED_TYPES if landlord else frozenset(),
    )
    counts = dict(Counter(f["entity_type"] for f in findings))
    logger.info(f"PII {pii_mode} page {page_num + 1}: {counts or 'none'}")
    if pii_mode == "enforce":
        apply_pii_redactions(page, findings)
    return findings


def redact_bank_generic(input_path, output_path, keep_keywords, keep_credits=False,
                        pii_mode="off", redact_balances=False):
    """Redact every transaction not matching ``keep_keywords`` from a generic UK
    bank statement. Returns (output_path, total_of_kept_amounts, kept_rows).

    When ``keep_credits`` is True (landlord mode), credit rows (money in) are
    always kept in addition to keyword matches — keywords may be empty.

    ``pii_mode`` controls an optional Presidio PII pass run *after* the
    transaction redaction, on each page, reusing the header_y already found by
    ``_redact_page``: ``"off"`` (default, byte-identical to no PII layer),
    ``"report"`` (log per-page counts by entity type) or ``"enforce"`` (also
    redact the findings). Both non-off modes require presidio-analyzer.
    In landlord mode (``keep_credits=True``) the PII pass preserves
    account-ownership details (holder name, account number, sort code, IBAN) —
    the statement must keep proving whose account received the rent. On
    headerless pages after the transaction pages (bank info/terms pages) only
    format-validated types are redacted, so the bank's own address and helpline
    numbers stay readable.

    ``redact_balances=True`` removes financial-summary amounts: the summary
    block above the transaction header (opening/closing balances, totals) and
    the running-balance column on every row, kept rows included — a kept row
    proves its own amount, never the account's running total. Regex/positional
    only; works without presidio and composes with any ``pii_mode``.
    """
    doc = fitz.open(input_path)
    kept_rows = []
    seen_header = False

    for page_num in range(len(doc)):
        page = doc[page_num]
        header_y = _redact_page(page, keep_keywords, kept_rows,
                                keep_credits=keep_credits,
                                redact_balances=redact_balances)
        if pii_mode in ("report", "enforce"):
            _apply_pii_pass(page, page_num, header_y, pii_mode,
                            structured_only=(header_y is None and seen_header),
                            landlord=keep_credits)
        seen_header = seen_header or header_y is not None

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
