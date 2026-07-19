"""Wise-style statements: dateless two-line transactions (Phase 2 extension).

Layout replicated from a real Wise GBP statement (synthesized here — no real
statements in the repo): header 'Description | Incoming | Outgoing | Amount'
(no Date column; 'Amount' is the running balance), each transaction is a
description+amounts row followed by a 'D Month YYYY | Transaction: REF' row,
and FCA boilerplate prose sits below the table.
"""
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from redact_bank_generic import redact_bank_generic
from provider_config import detect_provider

X_DESC, X_IN, X_OUT, X_BAL = 43, 384, 452, 525


def build_wise_statement(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((42, 60), "Wise Payments Ltd.")
    page.insert_text((44, 90), "Harriet Example")
    page.insert_text((42, 120), "1 June 2026 [GMT+01:00] - 30 June 2026 [GMT+01:00]")

    for text, x in [("Description", X_DESC), ("Incoming", X_IN),
                    ("Outgoing", X_OUT), ("Amount", X_BAL)]:
        page.insert_text((x, 384), text)

    # Tx 1 (debit): head row, then date+reference line 15pt below.
    page.insert_text((X_DESC - 1, 405), "Card payment to Coffee Shop")
    page.insert_text((X_OUT + 4, 405), "-4.50")
    page.insert_text((X_BAL + 12, 405), "95.50")
    page.insert_text((X_DESC - 1, 420), "4 June 2026")
    page.insert_text((91, 420), "Transaction: CARD-1111111111")

    # Tx 2 (credit): incoming amount.
    page.insert_text((X_DESC - 1, 441), "Received money from Employer Ltd")
    page.insert_text((X_IN + 8, 441), "500.00")
    page.insert_text((X_BAL + 3, 441), "595.50")
    page.insert_text((X_DESC - 1, 456), "9 June 2026")
    page.insert_text((91, 456), "Transaction: TRANSFER-2222222222")

    # Tx 3 (debit, keyword match).
    page.insert_text((X_DESC - 1, 470), "Rent to Letting Agent")
    page.insert_text((X_OUT + 4, 470), "-450.00")
    page.insert_text((X_BAL + 3, 470), "145.50")
    page.insert_text((X_DESC - 1, 485), "12 June 2026")
    page.insert_text((91, 485), "Transaction: TRANSFER-3333333333")

    # FCA boilerplate well below the table (must never be touched).
    page.insert_text((44, 530), "Wise Payments Limited is authorised by the Financial Conduct")
    page.insert_text((44, 542), "Authority under the Electronic Money Regulations 2017.")
    doc.save(str(path))
    doc.close()


def test_wise_dateless_layout_redaction(tmp_path):
    inp, out = tmp_path / "wise.pdf", tmp_path / "wise_out.pdf"
    build_wise_statement(inp)

    out_path, total, kept = redact_bank_generic(str(inp), str(out), ["rent"])
    text = fitz.open(out_path)[0].get_text()

    # Keyword transaction survives in full, including its date/reference line.
    assert "Rent to Letting Agent" in text
    assert "12 June 2026" in text
    # Non-keyword transactions are destroyed: descriptions, amounts, AND their
    # date/reference tail lines.
    for gone in ["Coffee Shop", "-4.50", "4 June 2026", "CARD-1111111111",
                 "Employer Ltd", "500.00", "9 June 2026", "TRANSFER-2222222222"]:
        assert gone not in text, gone
    # Running-balance column (labelled 'Amount') fully intact.
    for bal in ["95.50", "595.50", "145.50"]:
        assert bal in text
    # Page furniture and FCA boilerplate untouched.
    for keep in ["Harriet Example", "Wise Payments Ltd.",
                 "Financial Conduct", "Electronic Money Regulations"]:
        assert keep in text
    assert len(kept) == 1 and total == pytest.approx(450.00)


def test_wise_landlord_mode_keeps_incoming(tmp_path):
    inp, out = tmp_path / "wise2.pdf", tmp_path / "wise2_out.pdf"
    build_wise_statement(inp)

    _, total, kept = redact_bank_generic(str(inp), str(out), [], keep_credits=True)
    text = fitz.open(str(out))[0].get_text()
    assert "Employer Ltd" in text and "9 June 2026" in text   # credit kept
    assert "Coffee Shop" not in text and "Letting Agent" not in text
    assert len(kept) == 1 and total == pytest.approx(500.00)


def test_wise_detection_by_name():
    assert detect_provider("Wise Payments Ltd. statement of account") == "wise"
    assert detect_provider("visit wise.com/help for support") == "wise"
    # The bare word 'wise' must NOT trigger (e.g. 'Clockwise Ltd').
    assert detect_provider("Clockwise Ltd invoice, otherwise unrelated") is None
