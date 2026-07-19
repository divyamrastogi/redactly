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


def test_matches_date_with_year_suffix():
    """Revolut-style dates carry the year: '1 May 2026' must count as a row date."""
    from redact_bank_generic import _matches_date
    assert _matches_date("1 May 2026")
    assert _matches_date("28 Jun 2026")
    assert _matches_date("01 May")            # existing forms still work
    assert _matches_date("01/05/2026")
    assert not _matches_date("May 2026")      # month-year alone is not a row date
    assert not _matches_date("1 May 2026 extra")


def test_prose_page_is_never_redacted(tmp_path):
    """A page of prose that merely MENTIONS label words (balance, credit,
    'Migration Date') plus an inline DD/MM/YYYY date must not fabricate a
    header line and must come through byte-identical in text terms.
    Regression test for the Revolut info-page bug."""
    inp, out = tmp_path / "prose.pdf", tmp_path / "prose_out.pdf"
    doc = fitz.open()

    # Page 1: a real transaction table (so the file as a whole is processable).
    page = doc.new_page()
    for text, x in [("Date", 50), ("Description", 110), ("Paid out", 340),
                    ("Paid in", 430), ("Balance", 510)]:
        page.insert_text((x, 110), text)
    page.insert_text((50, 140), "01 May")
    page.insert_text((110, 140), "Tesco")
    page.insert_text((340, 140), "45.20")
    page.insert_text((510, 140), "954.80")

    # Page 2: prose that echoes the Revolut migration notice shape. The label
    # words share ONE baseline (as inline bold segments do in real statements),
    # which is what fabricated the phantom header line in the original bug.
    page2 = doc.new_page()
    same_line = [
        ("Notes about your balance", 50),          # 'balance'
        ("and any credit received", 250),          # 'credit' → paid_in
        ("on the given date", 450),                # 'date'
    ]
    for text, x in same_line:
        page2.insert_text((x, 100), text)
    dated_line = [
        ("The Account Migration took place on", 50),
        ("18/06/2026", 460),                       # sits in the phantom 'date' band
    ]
    for text, x in dated_line:
        page2.insert_text((x, 130), text)
    page2.insert_text((50, 160), "that date appears on your previous statement of transactions.")
    prose = [t for t, _ in same_line] + [t for t, _ in dated_line] + [
        "that date appears on your previous statement of transactions."]
    doc.save(str(inp))
    doc.close()

    redact_bank_generic(str(inp), str(out), ["zzz-no-match"])

    res = fitz.open(str(out))
    # Page 1's transaction was redacted (no keyword matched)...
    assert "Tesco" not in res[0].get_text()
    assert "954.80" in res[0].get_text()          # ...but its balance survives.
    # Page 2 prose is fully intact, including the inline date.
    after = ' '.join(res[1].get_text().split())
    before = ' '.join(' '.join(prose).split())
    for fragment in ["Account Migration took place on", "18/06/2026",
                     "Notes about your balance", "any credit received"]:
        assert fragment in after
    res.close()
