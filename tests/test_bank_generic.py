"""Tests for the generic UK bank-statement parser (Phase 2, Task 2.1).

A synthetic 1-page "bank statement" is built with PyMuPDF (fitz) — header with a
sort code + account-holder name (page furniture that must survive), a column
header row (Date / Description / Paid out / Paid in / Balance), and 6 transaction
rows at known positions. No real statements ever live in this repo.
"""
import os
import sys

import fitz
import pytest

# Make the repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from redact_bank_generic import redact_bank_generic
from provider_config import detect_provider


# Column X positions chosen so the description column has a wide band and every
# cell lands unambiguously in its own column regardless of font width.
X_DATE, X_DESC, X_PAID_OUT, X_PAID_IN, X_BAL = 50, 110, 340, 430, 510


def build_statement(path):
    """Write a synthetic 1-page UK bank statement PDF at ``path``.

    Row 4 ("Landlord Rent") is the one the keyword "rent" should keep.
    """
    doc = fitz.open()
    page = doc.new_page()

    # Page furniture (must NEVER be redacted): sort code + account holder name.
    page.insert_text((50, 60), "Sort Code 12-34-56")
    page.insert_text((50, 80), "Jane Smith")

    # Column header row.
    for text, x in [("Date", X_DATE), ("Description", X_DESC),
                    ("Paid out", X_PAID_OUT), ("Paid in", X_PAID_IN),
                    ("Balance", X_BAL)]:
        page.insert_text((x, 110), text)

    # Transaction rows: (date, description, paid_out, paid_in, balance).
    rows = [
        ("01 May", "Payroll",        None,      "2000.00", "2500.00"),
        ("03 May", "Tesco",          "45.20",   None,      "2454.80"),
        ("05 May", "Pret A Manger",  "8.75",    None,      "2446.05"),
        ("07 May", "Landlord Rent",  "1200.00", None,      "1246.05"),  # KEEP
        ("09 May", "Netflix",        "12.99",   None,      "1233.06"),
        ("11 May", "Amazon",         "33.50",   None,      "1199.56"),
    ]
    y = 140
    for date, desc, paid_out, paid_in, balance in rows:
        page.insert_text((X_DATE, y), date)
        page.insert_text((X_DESC, y), desc)
        if paid_out is not None:
            page.insert_text((X_PAID_OUT, y), paid_out)
        if paid_in is not None:
            page.insert_text((X_PAID_IN, y), paid_in)
        page.insert_text((X_BAL, y), balance)
        y += 22

    doc.save(str(path))
    doc.close()


def test_generic_bank_redaction_contract_and_text(tmp_path):
    inp = tmp_path / "statement.pdf"
    out = tmp_path / "redacted.pdf"
    build_statement(inp)

    out_path, total, kept = redact_bank_generic(str(inp), str(out), ["rent"])

    # --- Return contract (mirrors redact_barclaycard) ---
    assert os.path.exists(out_path)
    assert len(kept) == 1                      # only the rent row kept
    assert total == pytest.approx(1200.00)     # sum of kept amounts

    text = fitz.open(out_path)[0].get_text()

    # --- Keyword row survives (date + description + its amounts) ---
    assert "Landlord Rent" in text
    assert "07 May" in text
    assert "1200.00" in text                   # kept paid-out amount visible

    # --- Non-keyword descriptions are GONE after true redaction ---
    for gone in ["Payroll", "Tesco", "Pret A Manger", "Netflix", "Amazon"]:
        assert gone not in text, f"description '{gone}' should have been redacted"

    # --- Non-keyword dates are GONE ---
    for gone in ["01 May", "03 May", "05 May", "09 May", "11 May"]:
        assert gone not in text

    # --- Paid-in / paid-out cells of redacted rows are GONE ---
    for gone in ["2000.00", "45.20", "8.75", "12.99", "33.50"]:
        assert gone not in text, f"amount '{gone}' should have been redacted"

    # --- EVERY balance value survives (balance column is never redacted) ---
    for balance in ["2500.00", "2454.80", "2446.05",
                    "1246.05", "1233.06", "1199.56"]:
        assert balance in text, f"balance {balance} must never be redacted"

    # --- Page furniture / account-holder name untouched ---
    assert "Sort Code 12-34-56" in text
    assert "Jane Smith" in text


def test_keyword_match_is_case_insensitive(tmp_path):
    inp = tmp_path / "statement.pdf"
    out = tmp_path / "redacted.pdf"
    build_statement(inp)

    _, _, kept = redact_bank_generic(str(inp), str(out), ["RENT"])
    assert len(kept) == 1
    text = fitz.open(str(out))[0].get_text()
    assert "Landlord Rent" in text
    assert "Tesco" not in text


def test_detect_generic_bank_from_synthetic_pdf(tmp_path):
    """The synthetic statement has no card brand but does have sort-code /
    paid-in / paid-out markers, so detection returns generic_bank_uk."""
    inp = tmp_path / "statement.pdf"
    build_statement(inp)
    text = fitz.open(str(inp))[0].get_text()
    assert detect_provider(text) == 'generic_bank_uk'
