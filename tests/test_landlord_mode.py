"""Tests for landlord mode (Phase 3, Tasks 3.1–3.2).

Landlord mode (``keep_credits=True``) keeps every credit row (money in) plus any
keyword-matched rows, redacting all other debits while leaving the balance column
untouched. For credit-card statements landlord mode is rejected with a friendly
400. Sample statements are synthesized with PyMuPDF (fitz) — no real statements
ever live in this repo.
"""
import os
import sys

import fitz
import pytest

# Make the repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from redact_bank_generic import redact_bank_generic
import app as app_module
from app import app


# Column X positions (same geometry as test_bank_generic.py) so each cell lands
# unambiguously in its own discovered column band.
X_DATE, X_DESC, X_PAID_OUT, X_PAID_IN, X_BAL = 50, 110, 340, 430, 510

# Every amount/balance string in the fixture is unique, so presence/absence in
# the redacted text unambiguously attributes a value to its row.
CREDITS = [
    ("01 May", "Salary Payroll",   None,      "2500.00", "2510.00"),
    ("03 May", "Bank Transfer In", None,      "500.00",  "3010.00"),
]
DEBITS = [
    ("05 May", "Tesco",         "45.20",   None, "2964.80"),
    ("07 May", "Pret A Manger", "8.75",    None, "2956.05"),
    ("09 May", "Landlord Rent", "1200.00", None, "1756.05"),  # matches "rent"
    ("11 May", "Netflix",       "12.99",   None, "1743.06"),
]


def build_statement(path):
    """Synthetic 1-page UK bank statement.

    Two credit rows (amounts in the Paid in column) and four debit rows, one of
    which ("Landlord Rent") matches the keyword "rent".
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

    # rows: (date, description, paid_out, paid_in, balance)
    y = 140
    for date, desc, paid_out, paid_in, balance in CREDITS + DEBITS:
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


def _page_text(path):
    return fitz.open(str(path))[0].get_text()


ALL_BALANCES = ["2510.00", "3010.00", "2964.80", "2956.05", "1756.05", "1743.06"]


def test_landlord_mode_keeps_credits_and_keyword(tmp_path):
    """keep_credits=True + ['rent'] keeps the 2 credits and the rent row,
    destroys the other 3 debit descriptions, and leaves every balance visible."""
    inp = tmp_path / "statement.pdf"
    out = tmp_path / "redacted.pdf"
    build_statement(inp)

    out_path, total, kept = redact_bank_generic(str(inp), str(out), ["rent"], keep_credits=True)

    # 2 credits (paid-in amounts 2500 + 500) + the rent row (paid-out 1200).
    assert len(kept) == 3
    assert total == pytest.approx(2500.00 + 500.00 + 1200.00)
    assert os.path.exists(out_path)

    text = _page_text(out)

    # Kept rows survive — description text and their amounts.
    for kept_text in ["Salary Payroll", "Bank Transfer In", "Landlord Rent"]:
        assert kept_text in text, f"kept row '{kept_text}' should survive"
    for kept_amt in ["2500.00", "500.00", "1200.00"]:
        assert kept_amt in text

    # The other 3 debit descriptions are destroyed (true redaction).
    for gone in ["Tesco", "Pret A Manger", "Netflix"]:
        assert gone not in text, f"debit '{gone}' should have been redacted"
    for gone in ["45.20", "8.75", "12.99"]:
        assert gone not in text, f"debit amount '{gone}' should have been redacted"

    # Every balance survives (balance column is never redacted).
    for balance in ALL_BALANCES:
        assert balance in text, f"balance {balance} must survive"


def test_landlord_mode_no_keywords_keeps_only_credits(tmp_path):
    """keep_credits=True with NO keywords keeps only the credits — proving rent
    (a debit) is destroyed when there is no keyword to whitelist it."""
    inp = tmp_path / "statement.pdf"
    out = tmp_path / "redacted.pdf"
    build_statement(inp)

    out_path, total, kept = redact_bank_generic(str(inp), str(out), [], keep_credits=True)

    # Only the two credits.
    assert len(kept) == 2
    assert total == pytest.approx(2500.00 + 500.00)

    text = _page_text(out)
    for kept_text in ["Salary Payroll", "Bank Transfer In"]:
        assert kept_text in text

    # Every debit — including rent — is gone without a keyword.
    for gone in ["Tesco", "Pret A Manger", "Netflix", "Landlord Rent"]:
        assert gone not in text
    for gone in ["45.20", "8.75", "12.99", "1200.00"]:
        assert gone not in text

    for balance in ALL_BALANCES:
        assert balance in text, f"balance {balance} must survive"


def _write_card_pdf(path):
    """A tiny 1-page PDF whose text triggers barclaycard detection."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Barclaycard statement")
    doc.save(str(path))
    doc.close()


def test_landlord_mode_rejects_card_statement(tmp_path, monkeypatch):
    """mode=landlord against a detected card statement returns a friendly 400."""
    # Don't touch the real usage-counter file when exercising the endpoint.
    monkeypatch.setattr(app_module, "update_usage_counter", lambda: 0)

    card_pdf = tmp_path / "card.pdf"
    _write_card_pdf(card_pdf)

    client = app.test_client()
    with open(card_pdf, "rb") as f:
        resp = client.post("/redact", data={
            "pdf": (f, "card.pdf"),
            "provider": "auto",
            "mode": "landlord",
            "keywords": "",  # optional in landlord mode
        }, content_type="multipart/form-data")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == ("Landlord mode is for bank statements. "
                             "For card statements, use Expense mode with keywords.")
