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

# X-coordinate ranges for each column (with tolerance)
DATE_X_MIN, DATE_X_MAX   = 48,  82   # "06 Feb"
MERCH_X_MIN, MERCH_X_MAX = 80, 260   # merchant name (and wrapped lines)
AMT_X_MIN,   AMT_X_MAX   = 255, 295  # "£7.99"

DATE_PATTERN = re.compile(r'^\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$')
AMOUNT_PATTERN = re.compile(r'^£\d{1,3}(,\d{3})*\.\d{2}$')

# Section header that starts the transactions we care about
SECTION_START_TEXTS = ["How you've used your card", "How you\u2019ve used your card"]
# Section header that ends the transaction block
SECTION_END_TEXTS   = ["Promotional transactions", "Interest and charges"]


def is_in_x_range(bbox, x_min, x_max, tol=8):
    return (x_min - tol) <= bbox[0] <= (x_max + tol)


def group_transactions(page):
    """
    Parse page spans into a list of transaction dicts:
      { 'date': str, 'merchant': str, 'amount': float,
        'amount_bbox': Rect, 'merchant_bboxes': [Rect], 'date_bbox': Rect,
        'e_bboxes': [Rect] }
    Returns (transactions, section_start_y, section_end_y).
    """
    blocks = page.get_text("dict")["blocks"]

    # Collect all spans with position
    spans = []
    for block in blocks:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                spans.append({
                    "text": span["text"].strip(),
                    "bbox": span["bbox"],   # (x0, y0, x1, y1)
                })

    # Sort top→bottom, left→right
    spans.sort(key=lambda s: (round(s["bbox"][1] / 4), s["bbox"][0]))

    # Find section boundaries
    section_start_y = None
    section_end_y   = None
    for s in spans:
        if any(t in s["text"] for t in SECTION_START_TEXTS):
            section_start_y = s["bbox"][3]   # bottom of header
            logger.info(f"Transaction section starts at y={section_start_y:.1f}")
        if section_start_y and any(t in s["text"] for t in SECTION_END_TEXTS):
            section_end_y = s["bbox"][1]      # top of end header
            logger.info(f"Transaction section ends at y={section_end_y:.1f}")
            break

    if section_start_y is None:
        logger.warning("Could not find transaction section start — no transactions found")
        return [], None, None

    # Filter to only spans inside the transaction section
    tx_spans = [s for s in spans
                if s["bbox"][1] >= section_start_y
                and (section_end_y is None or s["bbox"][1] < section_end_y)
                and s["text"]]

    # Group into rows by y-coordinate (tolerance ±4 pts)
    rows = {}
    for s in tx_spans:
        y_key = round(s["bbox"][1] / 4) * 4
        rows.setdefault(y_key, []).append(s)

    # Identify transaction rows: a row with a date span at x≈52-77
    transactions = []
    sorted_y_keys = sorted(rows.keys())

    i = 0
    while i < len(sorted_y_keys):
        y = sorted_y_keys[i]
        row = rows[y]

        date_span = None
        merch_spans = []
        amount_span = None
        e_spans = []

        for s in row:
            bbox = s["bbox"]
            text = s["text"]
            if DATE_PATTERN.match(text) and is_in_x_range(bbox, DATE_X_MIN, DATE_X_MAX):
                date_span = s
            elif AMOUNT_PATTERN.match(text) and is_in_x_range(bbox, AMT_X_MIN, AMT_X_MAX):
                amount_span = s
            elif text == 'e' and is_in_x_range(bbox, 240, 260):
                e_spans.append(s)
            elif is_in_x_range(bbox, MERCH_X_MIN, MERCH_X_MAX):
                merch_spans.append(s)

        if date_span:
            # Also collect 'e' markers from other blocks at roughly same y
            # (they're sometimes in separate PDF line objects)
            tx_y = date_span["bbox"][1]
            for s in spans:
                if (s["text"].strip() == "e"
                        and is_in_x_range(s["bbox"], 235, 260)
                        and abs(s["bbox"][1] - tx_y) < 12
                        and s["bbox"] not in [x["bbox"] for x in e_spans]):
                    e_spans.append(s)

            # Check next row(s) for wrapped merchant name (no date, merchant x, no amount)
            j = i + 1
            while j < len(sorted_y_keys):
                next_y = sorted_y_keys[j]
                next_row = rows[next_y]
                has_date   = any(DATE_PATTERN.match(s["text"]) and is_in_x_range(s["bbox"], DATE_X_MIN, DATE_X_MAX) for s in next_row)
                has_amount = any(AMOUNT_PATTERN.match(s["text"]) and is_in_x_range(s["bbox"], AMT_X_MIN, AMT_X_MAX) for s in next_row)
                wrap_spans = [s for s in next_row if is_in_x_range(s["bbox"], MERCH_X_MIN, MERCH_X_MAX)]
                if not has_date and not has_amount and wrap_spans:
                    merch_spans.extend(wrap_spans)
                    j += 1
                else:
                    break

            merchant_text = " ".join(s["text"] for s in merch_spans).strip()
            amount_val    = float(amount_span["text"].replace("£","").replace(",","")) if amount_span else None

            transactions.append({
                "date":           date_span["text"],
                "merchant":       merchant_text,
                "amount":         amount_val,
                "date_bbox":      fitz.Rect(date_span["bbox"]),
                "merchant_bboxes":[fitz.Rect(s["bbox"]) for s in merch_spans],
                "amount_bbox":    fitz.Rect(amount_span["bbox"]) if amount_span else None,
                "e_bboxes":       [fitz.Rect(s["bbox"]) for s in e_spans],
            })
            i = j
        else:
            i += 1

    return transactions, section_start_y, section_end_y


def redact_barclaycard(input_path, output_path, keep_keywords):
    """
    Redact all transactions from a BarclayCard statement that don't match keep_keywords.
    Adds a sum total annotation for the kept transactions.
    """
    doc  = fitz.open(input_path)
    kept = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        transactions, section_start_y, section_end_y = group_transactions(page)

        if not transactions:
            continue

        logger.info(f"Page {page_num+1}: found {len(transactions)} transactions")

        for tx in transactions:
            merchant = tx["merchant"]
            is_kept  = any(kw.lower() in merchant.lower() for kw in keep_keywords)

            if is_kept:
                logger.info(f"  KEEP  {tx['date']} | {merchant} | £{tx['amount']:.2f}")
                if tx["amount"]:
                    kept.append(tx["amount"])
            else:
                logger.info(f"  REDACT {tx['date']} | {merchant} | £{tx['amount']:.2f}")
                # Redact the entire row as one full-width black bar
                # Collect all bboxes for this transaction to compute the full row extent
                all_bboxes = ([tx["date_bbox"]] +
                              tx["merchant_bboxes"] +
                              ([tx["amount_bbox"]] if tx["amount_bbox"] else []) +
                              tx["e_bboxes"])
                if all_bboxes:
                    y0 = min(r.y0 for r in all_bboxes) - 2
                    y1 = max(r.y1 for r in all_bboxes) + 2
                    # Full width from left margin to right margin of amount column
                    full_row = fitz.Rect(48, y0, 290, y1)
                    page.add_redact_annot(full_row, fill=(0, 0, 0))

        page.apply_redactions()

    total = sum(kept)
    logger.info(f"\nKept {len(kept)} transactions totalling £{total:.2f}")

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

    out, total, kept_amounts = redact_barclaycard(input_pdf, output_pdf, keywords)
    print(f"\nDone. Kept {len(kept_amounts)} transactions = £{total:.2f}")
    print(f"Output: {out}")
