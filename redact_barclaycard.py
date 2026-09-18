"""
BarclayCard statement transaction redaction.

Layout (from visual analysis of PDF spans):
  Page 2 transactions section:
  - Date:     x≈52-77,   format "DD Mon" (e.g. "06 Feb")
  - Merchant: x≈84-250,  may wrap to next line
  - 'e' mark: x≈244-250  (contactless indicator, single char 'e')
  - Amount:   x≈260-285, format "£XX.XX"

  Section starts after "How you've used your card" header (y≈174)
  Section ends at "Promotional transactions" or "Interest and charges"

Usage:
  python3 redact_barclaycard.py <input.pdf> <output.pdf> <keyword1> [keyword2 ...]

Example:
  python3 redact_barclaycard.py statement.pdf redacted.pdf "Hyperoptic" "Tfl Travel" "Your-Saving"
"""

import fitz
import re
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# BarclayCard uses up to TWO columns when there are many transactions.
# Left column:  date x≈52, merchant x≈84-250, amount x≈255-295
# Right column: date x≈330, merchant x≈361-520, amount x≈520-565
COLUMNS = [
    {"date": (48, 82),   "merch": (80, 260),  "amt": (250, 300), "e": (235, 265)},
    {"date": (325, 365), "merch": (355, 520),  "amt": (515, 570), "e": (515, 530)},
]

DATE_PATTERN        = re.compile(r'^\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$')
# Some PyMuPDF versions merge date + merchant into one span e.g. "24 Aug Tfl Travel CH..."
DATE_PREFIX_PATTERN = re.compile(r'^(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))\s+(.+)$')
AMOUNT_PATTERN      = re.compile(r'^£\d{1,3}(,\d{3})*\.\d{2}(CR)?$')

# Section header that starts the transactions we care about
SECTION_START_TEXTS = ["How you've used your card", "How you\u2019ve used your card"]
# Section header that ends the transaction block
SECTION_END_TEXTS   = ["Promotional transactions", "Interest and charges"]


def is_in_x_range(bbox, x_min, x_max, tol=8):
    return (x_min - tol) <= bbox[0] <= (x_max + tol)


def col_for_span(bbox):
    """Return the column config that matches a span's x position, or None."""
    for col in COLUMNS:
        d = col["date"]
        if (d[0] - 10) <= bbox[0] <= (d[1] + 200):  # wide check — any span in this col area
            return col
    return None


def group_transactions(page):
    """
    Parse page spans into transaction dicts. Handles 1 or 2 columns.
    Returns (transactions, section_start_y, section_end_y).
    """
    blocks = page.get_text("dict")["blocks"]

    # Collect all spans
    spans = []
    for block in blocks:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                spans.append({
                    "text": span["text"].strip(),
                    "bbox": span["bbox"],
                })

    # Find section boundaries across ALL columns
    # Section start = "How you've used your card" (any x)
    # Section end   = "Promotional transactions" or "Interest and charges" (any x)
    section_start_y = None
    section_end_y_by_col = {}  # col_index → end_y

    for s in sorted(spans, key=lambda x: x["bbox"][1]):
        text = s["text"]
        if any(t in text for t in SECTION_START_TEXTS) and section_start_y is None:
            section_start_y = s["bbox"][3]
            logger.info(f"Transaction section starts at y={section_start_y:.1f}")

    if section_start_y is None:
        logger.warning("Could not find transaction section start — no transactions found")
        return [], None, None

    # Find end markers — "Promotional transactions" can appear in either column.
    # Strategy: find ALL instances and assign to columns by x, but also track the
    # maximum y of any date-pattern span per column to use as a fallback end boundary.
    for s in spans:
        if any(t in s["text"] for t in SECTION_END_TEXTS):
            x0 = s["bbox"][0]
            # Assign to column by which column's x range it falls in
            assigned_col = None
            for ci, col in enumerate(COLUMNS):
                # Use tight column boundary — midpoint between columns is ~310
                col_mid = 310
                if ci == 0 and s["bbox"][0] < col_mid:
                    assigned_col = ci
                    break
                elif ci == 1 and s["bbox"][0] >= col_mid:
                    assigned_col = ci
                    break
            if assigned_col is None:
                assigned_col = 1 if x0 > 300 else 0
            if assigned_col not in section_end_y_by_col:
                section_end_y_by_col[assigned_col] = s["bbox"][1]
                logger.info(f"Column {assigned_col} section ends at y={s['bbox'][1]:.1f} ('{s['text'][:30]}')")

    # For any column without an explicit end marker, find the last date-pattern row
    # in that column as a fallback (prevents false truncation from cross-column text)
    for ci, col in enumerate(COLUMNS):
        if ci not in section_end_y_by_col:
            last_date_y = None
            for s in spans:
                if (DATE_PATTERN.match(s["text"].strip())
                        and is_in_x_range(s["bbox"], col["date"][0], col["date"][1])):
                    last_date_y = s["bbox"][3]
            if last_date_y:
                section_end_y_by_col[ci] = last_date_y + 20
                logger.info(f"Column {ci} section end inferred from last date: y={last_date_y:.1f}")

    # Process each column separately
    transactions = []

    for col_idx, col in enumerate(COLUMNS):
        d_min, d_max   = col["date"]
        m_min, m_max   = col["merch"]
        a_min, a_max   = col["amt"]
        e_min, e_max   = col["e"]
        end_y = section_end_y_by_col.get(col_idx, None)

        # Filter spans for this column.
        # Right column (col_idx>0) restarts y from top of page, so we can't
        # use section_start_y (which is the left-column header position).
        # Instead just filter by x range and section end y.
        col_spans = [
            s for s in spans
            if s["bbox"][0] >= (d_min - 10)
            and s["bbox"][0] <= (a_max + 20)
            and (end_y is None or s["bbox"][1] < end_y)
            and s["text"]
        ]
        # For left column, still enforce section_start_y
        if col_idx == 0:
            col_spans = [s for s in col_spans if s["bbox"][1] >= section_start_y]

        if not col_spans:
            continue

        # Group into rows by y
        rows = {}
        for s in col_spans:
            y_key = round(s["bbox"][1] / 4) * 4
            rows.setdefault(y_key, []).append(s)

        sorted_y_keys = sorted(rows.keys())
        i = 0
        while i < len(sorted_y_keys):
            y = sorted_y_keys[i]
            row = rows[y]

            date_span  = None
            merch_spans = []
            amount_span = None
            e_spans    = []

            for s in row:
                bbox = s["bbox"]
                text = s["text"]
                merged = DATE_PREFIX_PATTERN.match(text) if text else None
                if DATE_PATTERN.match(text) and is_in_x_range(bbox, d_min, d_max):
                    date_span = s
                elif merged and is_in_x_range(bbox, d_min, d_max + 300):
                    # Merged "DD Mon Merchant Name" span — split it
                    date_only  = merged.group(1)
                    merch_only = merged.group(2)
                    date_span  = {"text": date_only,  "bbox": bbox}
                    merch_spans.append({"text": merch_only, "bbox": bbox})
                elif AMOUNT_PATTERN.match(text) and is_in_x_range(bbox, a_min, a_max):
                    amount_span = s
                elif text == 'e' and is_in_x_range(bbox, e_min, e_max):
                    e_spans.append(s)
                elif is_in_x_range(bbox, m_min, m_max):
                    merch_spans.append(s)

            if date_span:
                # Collect 'e' markers from other blocks at same y
                tx_y = date_span["bbox"][1]
                for s in col_spans:
                    if (s["text"].strip() == "e"
                            and is_in_x_range(s["bbox"], e_min, e_max)
                            and abs(s["bbox"][1] - tx_y) < 12
                            and s["bbox"] not in [x["bbox"] for x in e_spans]):
                        e_spans.append(s)

                # Check for wrapped merchant lines
                j = i + 1
                while j < len(sorted_y_keys):
                    next_row = rows[sorted_y_keys[j]]
                    has_date   = any(DATE_PATTERN.match(s["text"]) and is_in_x_range(s["bbox"], d_min, d_max) for s in next_row)
                    has_amount = any(AMOUNT_PATTERN.match(s["text"]) and is_in_x_range(s["bbox"], a_min, a_max) for s in next_row)
                    # A lone 'e' contactless marker is never merchant text. When
                    # y-bucket rounding puts it in its own row, swallowing it here
                    # stretches the row's redaction bar over the NEXT transaction
                    # (the one the 'e' actually belongs to).
                    wrap_spans = [
                        s for s in next_row
                        if is_in_x_range(s["bbox"], m_min, m_max)
                        and not (s["text"].strip() == 'e' and is_in_x_range(s["bbox"], e_min, e_max))
                    ]
                    if not has_date and not has_amount and wrap_spans:
                        merch_spans.extend(wrap_spans)
                        j += 1
                    else:
                        break

                merchant_text = " ".join(s["text"] for s in merch_spans).strip()
                amount_val    = float(amount_span["text"].replace("£","").replace(",","").replace("CR","")) if amount_span else None
                # A credit (refund / money in) is a CR-suffixed amount — the same
                # marker commit 9408e1f taught the parser to tolerate.
                is_credit     = bool(amount_span and amount_span["text"].strip().upper().endswith("CR"))

                transactions.append({
                    "date":            date_span["text"],
                    "merchant":        merchant_text,
                    "amount":          amount_val,
                    "is_credit":       is_credit,
                    "date_bbox":       fitz.Rect(date_span["bbox"]),
                    "merchant_bboxes": [fitz.Rect(s["bbox"]) for s in merch_spans],
                    "amount_bbox":     fitz.Rect(amount_span["bbox"]) if amount_span else None,
                    "e_bboxes":        [fitz.Rect(s["bbox"]) for s in e_spans],
                    "col":             col_idx,
                    "full_row_x":      (d_min - 4, a_max + 4),  # full redaction width
                })
                i = j
            else:
                i += 1

    return transactions, section_start_y, section_end_y_by_col.get(0)


def redact_financial_summary(doc):
    """
    Redact financial summary fields from all pages:
    - Card number
    - New balance, previous balance, activity, interest, other charges
    - Minimum payment amount
    - Available to spend, credit limit
    - Interest rates and estimated interest
    - Payment amounts on page 2 summary
    Keeps: name, address, payment due DATE, website, phone numbers.
    """
    # Patterns whose values should be redacted (label + value on same or next span)
    REDACT_LABELS = [
        "Your new balance",
        "Minimum payment",
        "Your previous balance",
        "Payments towards your account",
        "Your new activity",
        "Interest charged",
        "Other charges",
        "Available to spend",
        "Your current credit limit",
        "Estimated interest next month",
        "Simple standard rate p.a",
        "Simple cash rate p.a",
        "Transactions, interest and charges",
        "Interest and charges",       # section header with total
        "Monthly Membership Fee",     # always redact this charge
        "Number ",                    # card number line
    ]
    # Standalone value patterns to always redact on financial pages
    REDACT_VALUE_RE = re.compile(
        r'^(£[\d,]+\.\d{2}|'           # £ amounts
        r'\d{4}\s+\d{4}\s+\d{4}\s+\d{4}|'  # card number
        r'\d+\.\d+%|'                  # interest rates
        r'\(\d+\.\d+%\s+compound.*\))$',  # compound equivalent
        re.IGNORECASE
    )

    for page_num in range(len(doc)):
        page = doc[page_num]
        spans = []
        for block in page.get_text("dict")["blocks"]:
            if "lines" not in block: continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if span["text"].strip():
                        spans.append(span)

        # Sort by y then x
        spans.sort(key=lambda s: (round(s["bbox"][1]/4)*4, s["bbox"][0]))

        # Mark which y-rows contain a redactable label, AND which column (x) the label is in.
        # Key: (y_key, col_side) where col_side = 'left' (x<300) or 'right' (x>=300)
        # This prevents cross-column collateral: e.g. "Minimum payment" (right col) at the
        # same y as a TFL transaction (left col) must NOT redact the TFL amount.
        redact_y_col = {}  # y_key -> set of col_sides ('left', 'right')
        for s in spans:
            t = s["text"].strip()
            if any(lbl.lower() in t.lower() for lbl in REDACT_LABELS):
                y_key = round(s["bbox"][1] / 4) * 4
                col_side = 'right' if s["bbox"][0] >= 300 else 'left'
                redact_y_col.setdefault(y_key, set()).add(col_side)

        for s in spans:
            t    = s["text"].strip()
            bbox = s["bbox"]
            rect = fitz.Rect(bbox)
            y_key = round(bbox[1] / 4) * 4

            # Redact card number span
            if re.match(r'\d{4}\s+\d{4}\s+\d{4}\s+\d{4}', t) or \
               re.match(r'Number\s+\d{4}', t):
                page.add_redact_annot(rect, fill=(0, 0, 0))
                continue

            # Redact values on labelled rows (amounts, rates) — same column only
            if y_key in redact_y_col and REDACT_VALUE_RE.match(t):
                span_col = 'right' if bbox[0] >= 300 else 'left'
                if span_col in redact_y_col[y_key]:
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    continue

            # On page 2 LEFT column only: redact summary amounts ABOVE transaction section
            # (Direct Debit payment line, "Transactions total" subtotals)
            # Use section_start_y (≈183) as boundary — don't touch transaction rows below it
            if page_num == 1 and bbox[1] < 182 and bbox[0] < 300:
                if REDACT_VALUE_RE.match(t) and t.startswith('£'):
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    continue

            # Redact "Interest and charges" header row + Monthly Membership Fee row
            if 'Interest and charges' in t or 'Monthly Membership Fee' in t:
                # Redact entire row width (covers date + label + amount regardless of column)
                x_min = 48 if bbox[0] < 300 else 325
                x_max = 295 if bbox[0] < 300 else 570
                row_rect = fitz.Rect(x_min, bbox[1] - 2, x_max, bbox[3] + 2)
                page.add_redact_annot(row_rect, fill=(0, 0, 0))
                continue

        page.apply_redactions()


def redact_barclaycard(input_path, output_path, keep_keywords, keep_credits=False):
    """
    Redact all transactions from a BarclayCard statement that don't match keep_keywords.
    Adds a sum total annotation for the kept transactions.

    When ``keep_credits`` is True, credit rows (CR-suffixed refunds / money in)
    are kept in addition to keyword matches.
    """
    doc  = fitz.open(input_path)
    kept = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        transactions, section_start_y, section_end_y = group_transactions(page)

        if not transactions:
            continue

        logger.info(f"Page {page_num+1}: found {len(transactions)} transactions")

        def _tx_kept(tx):
            return (any(kw.lower() in tx["merchant"].lower() for kw in keep_keywords)
                    or (keep_credits and tx.get("is_credit")))

        redact_rects = []
        for tx in transactions:
            merchant = tx["merchant"]
            is_kept  = (any(kw.lower() in merchant.lower() for kw in keep_keywords)
                        or (keep_credits and tx.get("is_credit")))

            if is_kept:
                amt_str = f"£{tx['amount']:.2f}" if tx['amount'] is not None else "£?.??"
                logger.info(f"  KEEP  {tx['date']} | {merchant} | {amt_str}")
                if tx["amount"] is not None:
                    kept.append(tx["amount"])
            else:
                amt_str = f"£{tx['amount']:.2f}" if tx['amount'] is not None else "£?.??"
                logger.info(f"  REDACT {tx['date']} | {merchant} | {amt_str}")
                # Redact the entire row as one full-width black bar
                all_bboxes = ([tx["date_bbox"]] +
                              tx["merchant_bboxes"] +
                              ([tx["amount_bbox"]] if tx["amount_bbox"] else []) +
                              tx["e_bboxes"])
                if all_bboxes:
                    y0 = min(r.y0 for r in all_bboxes) - 2
                    y1 = max(r.y1 for r in all_bboxes) + 2
                    x0, x1 = tx.get("full_row_x", (48, 290))
                    redact_rects.append(fitz.Rect(x0, y0, x1, y1))

        # Whitelisted rows must survive: apply_redactions deletes any text whose
        # bbox intersects a redaction rect, so clamp every rect away from kept
        # rows' spans (row padding can bleed into an adjacent row).
        kept_boxes = []
        for tx in transactions:
            if _tx_kept(tx):
                kept_boxes.extend([tx["date_bbox"]] + tx["merchant_bboxes"] +
                                  ([tx["amount_bbox"]] if tx["amount_bbox"] else []) +
                                  tx["e_bboxes"])
        for rect in redact_rects:
            for kb in kept_boxes:
                if rect.intersects(kb):
                    if kb.y0 >= rect.y0 and kb.y1 > rect.y1:
                        # kept box hangs below the rect — cut the rect's bottom
                        rect.y1 = min(rect.y1, kb.y0 - 0.5)
                    elif kb.y1 <= rect.y1 and kb.y0 < rect.y0:
                        # kept box hangs above the rect — cut the rect's top
                        rect.y0 = max(rect.y0, kb.y1 + 0.5)
            if rect.y1 > rect.y0 and rect.x1 > rect.x0:
                page.add_redact_annot(rect, fill=(0, 0, 0))
            else:
                logger.warning(f"  redaction rect collapsed after clamping: {rect}")

        page.apply_redactions()

    total = sum(kept)
    logger.info(f"\nKept {len(kept)} transactions totalling £{total:.2f}")

    # Redact financial summary fields (balances, limits, rates, card number)
    redact_financial_summary(doc)

    # Add total annotation on last transaction page (page 2 = index 1)
    if len(doc) > 1:
        ann_page = doc[1]
        # Place below the transaction section
        ann_y = 545 if section_end_y is None else section_end_y + 15
        rect   = fitz.Rect(48, ann_y, 300, ann_y + 18)
        ann_page.insert_textbox(
            rect,
            f"Whitelisted transactions total: £{total:.2f}",
            fontsize=9,
            color=(0, 0.4, 0),
            fontname="helv",
        )

    doc.save(output_path)
    doc.close()

    logger.info(f"Saved: {output_path}")
    return output_path, total, kept


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    input_pdf  = sys.argv[1]
    output_pdf = sys.argv[2]
    keywords   = sys.argv[3:]

    # Use a temp name first, then rename with total
    import tempfile
    tmp_out = output_pdf
    out, total, kept_amounts = redact_barclaycard(input_pdf, tmp_out, keywords)

    # Rename to include total amount
    base = os.path.splitext(output_pdf)[0]
    final_out = f"{base}_£{total:.2f}.pdf"
    os.rename(out, final_out)

    print(f"\nDone. Kept {len(kept_amounts)} transactions = £{total:.2f}")
    print(f"Output: {final_out}")
