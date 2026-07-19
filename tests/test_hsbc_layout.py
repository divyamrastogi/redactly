"""HSBC-style statements: fragmented headers, DD MMM YY dates, multi-line
transactions with amounts on the continuation line (synthetic — no real
statements in the repo)."""
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from redact_bank_generic import redact_bank_generic


def build_hsbc_statement(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((61, 60), "MR A B EXAMPLE")
    page.insert_text((360, 80), "Sort Code 40-11-22")

    # Fragmented header row, exactly as HSBC renders it: '£','Paid','out' are
    # separate spans; 'Payment type and details' splits leaving 'details' alone.
    for text, x in [("Date", 53), ("Payment type and", 117), ("details", 211),
                    ("£", 350), ("Paid", 357), ("out", 376),
                    ("£", 438), ("Paid", 445), ("in", 464),
                    ("£", 513), ("Balance", 520)]:
        page.insert_text((x, 440), text)

    page.insert_text((53, 468), "01 Jun 26")
    page.insert_text((140, 468), "BALANCE BROUGHT FORWARD")
    page.insert_text((529, 468), "800.00")

    # Single-line debit.
    page.insert_text((53, 480), "02 Jun 26")
    page.insert_text((113, 480), "DD")
    page.insert_text((140, 480), "ELECTRIC CO")
    page.insert_text((372, 480), "67.89")
    page.insert_text((529, 480), "750.00")

    # Multi-line credit: date row first, amount on the continuation line.
    page.insert_text((53, 492), "03 Jun 26")
    page.insert_text((113, 492), "CR")
    page.insert_text((140, 492), "EMPLOYER SALARY")
    page.insert_text((140, 504), "PAYROLL JUN")
    page.insert_text((450, 504), "2000.00")
    page.insert_text((522, 504), "2,750.00")

    # Multi-line debit with keyword, amount on continuation line.
    page.insert_text((53, 516), "04 Jun 26")
    page.insert_text((113, 516), "SO")
    page.insert_text((140, 516), "LETTING AGENT RENT")
    page.insert_text((140, 528), "REF 12345")
    page.insert_text((367, 528), "950.00")
    page.insert_text((522, 528), "1,800.00")

    # Single-line debit (no keyword).
    page.insert_text((53, 540), "05 Jun 26")
    page.insert_text((113, 540), "VIS")
    page.insert_text((140, 540), "COFFEE BAR")
    page.insert_text((372, 540), "4.20")
    page.insert_text((522, 540), "1,795.80")

    page.insert_text((53, 560), "30 Jun 26")
    page.insert_text((140, 560), "BALANCE CARRIED FORWARD")
    page.insert_text((522, 560), "1,795.80")
    doc.save(str(path))
    doc.close()


def test_hsbc_layout_keyword_redaction(tmp_path):
    inp, out = tmp_path / "hsbc.pdf", tmp_path / "hsbc_out.pdf"
    build_hsbc_statement(inp)

    _, total, kept = redact_bank_generic(str(inp), str(out), ["rent"])
    text = fitz.open(str(out))[0].get_text()

    # Keyword transaction survives in full, including its continuation line.
    for keep in ["LETTING AGENT RENT", "REF 12345", "950.00", "04 Jun 26"]:
        assert keep in text, keep
    # Non-keyword transactions destroyed — descriptions AND their amounts,
    # including amounts that sit on continuation lines.
    for gone in ["ELECTRIC CO", "67.89", "EMPLOYER SALARY", "PAYROLL JUN",
                 "2000.00", "COFFEE BAR", "4.20"]:
        assert gone not in text, gone
    # Balance column and brought/carried-forward rows always survive.
    for keep in ["800.00", "750.00", "2,750.00", "1,800.00", "1,795.80",
                 "BALANCE BROUGHT FORWARD", "BALANCE CARRIED FORWARD",
                 "MR A B EXAMPLE"]:
        assert keep in text, keep
    assert len(kept) == 1 and total == pytest.approx(950.00)


def test_hsbc_landlord_mode(tmp_path):
    inp, out = tmp_path / "hsbc2.pdf", tmp_path / "hsbc2_out.pdf"
    build_hsbc_statement(inp)

    _, total, kept = redact_bank_generic(str(inp), str(out), ["rent"], keep_credits=True)
    text = fitz.open(str(out))[0].get_text()
    # Salary credit (amount in Paid in band on continuation line) + rent kept.
    for keep in ["EMPLOYER SALARY", "2000.00", "LETTING AGENT RENT"]:
        assert keep in text, keep
    for gone in ["ELECTRIC CO", "COFFEE BAR"]:
        assert gone not in text, gone
    assert len(kept) == 2
