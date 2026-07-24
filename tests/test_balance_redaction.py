"""Tests for ``redact_balances`` — the financial-summary pass in
``redact_bank_generic``.

Pure regex/positional feature: needs no presidio, so these tests run on
minimal installs too. All PDFs are synthetic, built in-test.
"""
import hashlib
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from redact_bank_generic import redact_bank_generic

# Mirror the column layout used across the synthetic-statement suites.
X_DATE, X_DESC, X_PAID_OUT, X_PAID_IN, X_BAL = 50, 110, 340, 430, 510
HEADER_LABELS = ["Date", "Description", "Paid out", "Paid in", "Balance"]

OPENING_BALANCE = "9,876.54"
RENT_AMOUNT = "1200.00"
RENT_BALANCE = "1246.05"
DEBIT_BALANCE = "1233.06"


def _build_statement(path):
    """Synthetic dated statement: summary block above the header, one credit
    row (rent) and one debit row, each with a running balance."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 40), "Jane Smith")
    page.insert_text((50, 60), "Sort code 12-34-56")
    # Label and value as SEPARATE spans, as real statements render them (the
    # amount check matches whole spans; a fused "label 9,876.54" span is a
    # known miss, asserted separately below).
    page.insert_text((350, 60), "Opening balance")
    page.insert_text((460, 60), OPENING_BALANCE)
    for text, x in zip(HEADER_LABELS, [X_DATE, X_DESC, X_PAID_OUT, X_PAID_IN, X_BAL]):
        page.insert_text((x, 110), text)
    # Credit row (rent received) and a debit row.
    page.insert_text((X_DATE, 140), "01 May")
    page.insert_text((X_DESC, 140), "Rent received")
    page.insert_text((X_PAID_IN, 140), RENT_AMOUNT)
    page.insert_text((X_BAL, 140), RENT_BALANCE)
    page.insert_text((X_DATE, 162), "02 May")
    page.insert_text((X_DESC, 162), "Netflix")
    page.insert_text((X_PAID_OUT, 162), "12.99")
    page.insert_text((X_BAL, 162), DEBIT_BALANCE)
    doc.save(str(path))
    doc.close()


def _page_text(path):
    doc = fitz.open(str(path))
    text = "".join(doc[p].get_text() for p in range(len(doc)))
    doc.close()
    return text


def test_balances_redacted_kept_amount_survives(tmp_path):
    """With ``redact_balances=True`` (landlord mode): the summary balance above
    the header and the running balance of EVERY row disappear, while the kept
    row's own amount and description survive."""
    inp = tmp_path / "statement.pdf"
    out = tmp_path / "redacted.pdf"
    _build_statement(inp)

    redact_bank_generic(str(inp), str(out), [], keep_credits=True,
                        redact_balances=True)
    text = _page_text(out)

    assert OPENING_BALANCE not in text, "summary balance above header must go"
    assert RENT_BALANCE not in text, "kept row's running balance must go"
    assert DEBIT_BALANCE not in text, "redacted row's running balance must go"
    assert RENT_AMOUNT in text, "the kept credit's own amount must survive"
    assert "Rent received" in text
    # Non-amount furniture above the header is untouched by this pass.
    assert "Jane Smith" in text
    assert "12-34-56" in text


def test_redact_balances_composes_with_keywords(tmp_path):
    """Keyword mode: same guarantees when rows are kept by keyword rather than
    by credit."""
    inp = tmp_path / "statement.pdf"
    out = tmp_path / "redacted.pdf"
    _build_statement(inp)

    redact_bank_generic(str(inp), str(out), ["rent"], redact_balances=True)
    text = _page_text(out)

    assert RENT_AMOUNT in text
    assert RENT_BALANCE not in text
    assert OPENING_BALANCE not in text


def test_default_off_is_byte_identical(tmp_path):
    """``redact_balances`` defaults off and changes nothing — same text output
    as not passing the kwarg at all."""
    inp = tmp_path / "statement.pdf"
    out_flag = tmp_path / "flag_off.pdf"
    out_def = tmp_path / "default.pdf"
    _build_statement(inp)

    redact_bank_generic(str(inp), str(out_flag), ["rent"], redact_balances=False)
    redact_bank_generic(str(inp), str(out_def), ["rent"])

    def h(p):
        return hashlib.md5(_page_text(p).encode()).hexdigest()

    assert h(out_flag) == h(out_def)
    # And the default output still shows balances (the pre-existing behaviour
    # this flag exists to fix).
    assert RENT_BALANCE in _page_text(out_def)
