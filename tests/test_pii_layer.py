"""Tests for the Presidio PII layer (``pii_layer.py``).

All statement PDFs here are SYNTHETIC, built in-test with PyMuPDF
``insert_text`` — no real statement content is ever used. The whole suite is
skipped when ``presidio_analyzer`` is not installed, so the repo stays green on
minimal installs that only have the transaction-redaction dependencies.
"""
import hashlib
import os
import sys

import fitz
import pytest

# Skip the entire suite where presidio isn't installed.
presidio = pytest.importorskip("presidio_analyzer")

# Make the repo root importable regardless of where pytest is invoked from.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import pii_layer
from pii_layer import analyze_page_pii, apply_pii_redactions
from redact_bank_generic import _collect_spans, _detect_columns, redact_bank_generic

# Column X positions (mirror tests/test_bank_generic.py) so a planted header
# line resolves to a credible transaction table.
X_DATE, X_DESC, X_PAID_OUT, X_PAID_IN, X_BAL = 50, 110, 340, 430, 510
HEADER_LABELS = ["Date", "Description", "Paid out", "Paid in", "Balance"]
HEADER_Y = 165  # baseline of the header row


def _draw_header(page, y=HEADER_Y):
    """Draw the transaction column-header row; returns nothing."""
    for text, x in zip(HEADER_LABELS, [X_DATE, X_DESC, X_PAID_OUT, X_PAID_IN, X_BAL]):
        page.insert_text((x, y), text)


def _detect_header_y(page):
    """header_y exactly as the redactor/wire-in computes it."""
    _, header_y = _detect_columns(_collect_spans(page), page.rect.width)
    return header_y


def _rects_overlap(a, b):
    """Do two (x0, y0, x1, y1) rects intersect?"""
    return (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def _entity_types(findings):
    return {f["entity_type"] for f in findings}


# A Luhn-valid test card number (Stripe's 4242… test card) so Presidio's
# checksum-validated CREDIT_CARD recognizer reports it.
CARD_NUMBER = "4242424242424242"
ACCOUNT_NUMBER = "12345678"
SORT_CODE = "12-34-56"
NINO = "AB123456C"
IBAN = "GB29NWBK60161331926819"  # well-known valid GB IBAN (mod-97 passes)


def test_header_region_pii_all_reported(tmp_path):
    """Sort code, 8-digit account number, NI number, IBAN and a person name,
    planted in the page-furniture region ABOVE the transaction header, are all
    reported."""
    pdf = tmp_path / "header_pii.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # PII in the header (furniture) region, well above HEADER_Y.
    page.insert_text((50, 60), f"Sort code {SORT_CODE}")
    page.insert_text((50, 78), f"Account {ACCOUNT_NUMBER}")
    page.insert_text((50, 96), f"National Insurance {NINO}")
    page.insert_text((50, 114), f"IBAN {IBAN}")
    page.insert_text((50, 132), "Account holder Jane Smith")
    _draw_header(page)
    # One transaction row below the header (its merchant must NOT count here).
    page.insert_text((X_DATE, 195), "01 May")
    page.insert_text((X_DESC, 195), "Tesco")
    page.insert_text((X_PAID_OUT, 195), "12.50")
    page.insert_text((X_BAL, 195), "980.00")
    doc.save(str(pdf))
    doc.close()

    page = fitz.open(str(pdf))[0]
    header_y = _detect_header_y(page)
    assert header_y is not None, "test layout must yield a detected header"

    findings = analyze_page_pii(page, header_y=header_y)
    types = _entity_types(findings)

    assert "UK_SORT_CODE" in types
    assert "UK_ACCOUNT_NUMBER" in types
    assert "UK_NINO" in types
    assert "IBAN_CODE" in types
    assert "PERSON" in types

    # Privacy contract: a finding never carries the matched text.
    for f in findings:
        assert set(f.keys()) <= {"entity_type", "score", "bbox", "page_number"}
        assert isinstance(f["bbox"], tuple) and len(f["bbox"]) == 4


def test_merchant_below_header_not_reported_but_card_is(tmp_path):
    """A merchant-like name below the transaction header is NOT reported, while
    a 16-digit card number below the header IS — the STRUCTURED_TYPES rule."""
    pdf = tmp_path / "merchant.pdf"
    doc = fitz.open()
    page = doc.new_page()
    _draw_header(page)
    # Merchant-like description below the header.
    page.insert_text((X_DATE, 195), "01 May")
    page.insert_text((X_DESC, 195), "J SMITH BUTCHERS")
    page.insert_text((X_PAID_OUT, 195), "12.50")
    page.insert_text((X_BAL, 195), "980.00")
    # Card number below the header, with the 'Card' context word.
    page.insert_text((X_DESC, 220), f"Card {CARD_NUMBER}")
    doc.save(str(pdf))
    doc.close()

    page = fitz.open(str(pdf))[0]
    header_y = _detect_header_y(page)
    assert header_y is not None

    findings = analyze_page_pii(page, header_y=header_y)

    below = [f for f in findings if f["bbox"][1] > header_y]
    below_types = _entity_types(below)

    # The rule under test: structured types may be reported below the header,
    # person-shaped ones may not.
    assert "CREDIT_CARD" in pii_layer.STRUCTURED_TYPES
    assert "PERSON" not in pii_layer.STRUCTURED_TYPES

    # The card number (structured) survives below the header...
    assert "CREDIT_CARD" in below_types
    # ...but a person-shaped finding below the header never does: the
    # STRUCTURED_TYPES guard drops merchant names. (ORG / DATE_TIME / etc. are
    # not requested at all, so they can't appear either.)
    assert "PERSON" not in below_types


def test_offset_to_bbox_mapping_overlaps_inserted_rect(tmp_path):
    """The reported bbox for a known planted IBAN overlaps the rect where it
    was inserted on the page."""
    pdf = tmp_path / "mapping.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), f"IBAN {IBAN}")
    doc.save(str(pdf))
    doc.close()

    page = fitz.open(str(pdf))[0]
    # No transaction header → whole-page analysis (header_y=None).
    findings = analyze_page_pii(page, header_y=None)
    iban_findings = [f for f in findings if f["entity_type"] == "IBAN_CODE"]
    assert iban_findings, "expected the planted IBAN to be reported"

    inserted_rects = page.search_for(IBAN)
    assert inserted_rects, "planted IBAN must be locatable on the page"
    inserted = inserted_rects[0]

    assert any(_rects_overlap(f["bbox"], inserted) for f in iban_findings), (
        "reported IBAN bbox should overlap the rect where the text was inserted"
    )


def test_enforce_mode_removes_account_number(tmp_path):
    """After apply_pii_redactions, the planted account number is gone from the
    page text."""
    pdf = tmp_path / "enforce.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), f"Account {ACCOUNT_NUMBER}")
    doc.save(str(pdf))
    doc.close()

    page = fitz.open(str(pdf))[0]
    assert ACCOUNT_NUMBER in page.get_text()  # sanity: present before redaction

    findings = analyze_page_pii(page, header_y=None)
    assert any(f["entity_type"] == "UK_ACCOUNT_NUMBER" for f in findings)

    apply_pii_redactions(page, findings)

    assert ACCOUNT_NUMBER not in page.get_text()


def _build_statement(path):
    """Write a synthetic 1-page statement with a header + a few rows (mirrors
    tests/test_bank_generic.build_statement) for byte-identity checks."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), "Sort Code 12-34-56")
    page.insert_text((50, 80), "Jane Smith")
    _draw_header(page, y=110)
    rows = [
        ("01 May", "Tesco", "45.20", "954.80"),
        ("03 May", "Landlord Rent", "1200.00", "1246.05"),
        ("05 May", "Netflix", "12.99", "1233.06"),
    ]
    y = 140
    for date, desc, paid_out, balance in rows:
        page.insert_text((X_DATE, y), date)
        page.insert_text((X_DESC, y), desc)
        page.insert_text((X_PAID_OUT, y), paid_out)
        page.insert_text((X_BAL, y), balance)
        y += 22
    doc.save(str(path))
    doc.close()


def _text_hash(path):
    doc = fitz.open(str(path))
    text = "".join(doc[p].get_text() for p in range(len(doc)))
    doc.close()
    return hashlib.md5(text.encode()).hexdigest()


def test_pii_mode_off_is_byte_identical_to_default(tmp_path):
    """``pii_mode='off'`` produces identical text output to not passing the
    kwarg at all."""
    inp = tmp_path / "statement.pdf"
    out_off = tmp_path / "off.pdf"
    out_def = tmp_path / "default.pdf"
    _build_statement(inp)

    redact_bank_generic(str(inp), str(out_off), ["rent"], pii_mode="off")
    redact_bank_generic(str(inp), str(out_def), ["rent"])  # default

    assert _text_hash(out_off) == _text_hash(out_def)


def test_structured_only_drops_bank_contact_details(tmp_path):
    """On an info/terms page (``structured_only=True``) the bank's own
    person-shaped and phone-shaped details are NOT reported, while structured
    types still are."""
    pdf = tmp_path / "info_page.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # Bank furniture as found on real info pages: contact person, helpline.
    page.insert_text((50, 60), "Contact John Barclay, Customer Services")
    page.insert_text((50, 80), "Call us on 0345 734 5345")
    # A structured leak on the same page must still be caught.
    page.insert_text((50, 100), f"IBAN {IBAN}")
    doc.save(str(pdf))
    doc.close()

    page = fitz.open(str(pdf))[0]

    plain = _entity_types(analyze_page_pii(page, header_y=None))
    structured = _entity_types(
        analyze_page_pii(page, header_y=None, structured_only=True))

    assert "IBAN_CODE" in structured
    assert "PERSON" not in structured
    assert "PHONE_NUMBER" not in structured
    # Sanity: without the flag the same page DOES yield person/phone findings,
    # so the assertion above is proving the flag, not a detection failure.
    assert {"PERSON", "PHONE_NUMBER"} & plain


def test_landlord_excludes_ownership_pii(tmp_path):
    """``exclude_types=LANDLORD_PRESERVED_TYPES`` drops the account-ownership
    entities (name, account number, sort code, IBAN) while other PII (NINO) is
    still reported."""
    pdf = tmp_path / "landlord_page.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), f"Sort code {SORT_CODE}")
    page.insert_text((50, 78), f"Account {ACCOUNT_NUMBER}")
    page.insert_text((50, 96), f"IBAN {IBAN}")
    page.insert_text((50, 114), "Account holder Jane Smith")
    page.insert_text((50, 132), f"National Insurance {NINO}")
    doc.save(str(pdf))
    doc.close()

    page = fitz.open(str(pdf))[0]
    types = _entity_types(analyze_page_pii(
        page, header_y=None,
        exclude_types=pii_layer.LANDLORD_PRESERVED_TYPES))

    assert not (types & pii_layer.LANDLORD_PRESERVED_TYPES)
    assert "UK_NINO" in types


def test_excluded_detection_shields_overlapping_finding(tmp_path, monkeypatch):
    """A finding overlapping an excluded-type detection is dropped: preservation
    wins over a colliding recognizer.

    Regression for a real statement where the phone recognizer matched the
    whole "sort code + account number" digit run, so the account line was
    redacted in landlord mode despite UK_ACCOUNT_NUMBER being excluded. Uses a
    stubbed analyzer so the overlap is deterministic, independent of the real
    phone recognizer's behaviour."""
    from presidio_analyzer import RecognizerResult

    pdf = tmp_path / "shield.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), f"Sort code 123456 {ACCOUNT_NUMBER}")
    doc.save(str(pdf))
    doc.close()
    page = fitz.open(str(pdf))[0]

    class _StubAnalyzer:
        def analyze(self, text, entities, language):
            acc_start = text.index(ACCOUNT_NUMBER)
            return [
                # The preserved detection...
                RecognizerResult("UK_ACCOUNT_NUMBER", acc_start,
                                 acc_start + len(ACCOUNT_NUMBER), 0.65),
                # ...and a colliding phone match swallowing the same digits.
                RecognizerResult("PHONE_NUMBER", acc_start - 7,
                                 acc_start + len(ACCOUNT_NUMBER), 0.75),
            ]

    monkeypatch.setattr(pii_layer, "_analyzer", _StubAnalyzer())

    shielded = analyze_page_pii(
        page, header_y=None, exclude_types=frozenset({"UK_ACCOUNT_NUMBER"}))
    assert shielded == [], "phone match over preserved chars must be dropped"

    # Without exclusion the same stub yields both findings — proving the empty
    # result above comes from the shield, not from detection failure.
    plain = _entity_types(analyze_page_pii(page, header_y=None))
    assert plain == {"UK_ACCOUNT_NUMBER", "PHONE_NUMBER"}


def test_landlord_enforce_preserves_name_and_account(tmp_path):
    """End-to-end: landlord mode (``keep_credits=True``) with
    ``pii_mode='enforce'`` keeps the holder name and account number visible
    (proof of whose account received the rent) while still removing the NINO."""
    inp = tmp_path / "landlord_statement.pdf"
    out = tmp_path / "landlord_redacted.pdf"

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 40), "Jane Smith")
    page.insert_text((50, 60), f"Account {ACCOUNT_NUMBER}")
    page.insert_text((50, 80), f"National Insurance {NINO}")
    _draw_header(page, y=110)
    # One credit row (rent received: amount in Paid in) and one debit row.
    page.insert_text((X_DATE, 140), "01 May")
    page.insert_text((X_DESC, 140), "Rent received")
    page.insert_text((X_PAID_IN, 140), "1200.00")
    page.insert_text((X_BAL, 140), "1200.00")
    page.insert_text((X_DATE, 162), "02 May")
    page.insert_text((X_DESC, 162), "Netflix")
    page.insert_text((X_PAID_OUT, 162), "12.99")
    page.insert_text((X_BAL, 162), "1187.01")
    doc.save(str(inp))
    doc.close()

    redact_bank_generic(str(inp), str(out), [], keep_credits=True,
                        pii_mode="enforce")

    text = fitz.open(str(out))[0].get_text()
    assert "Jane Smith" in text, "landlord mode must keep the holder name"
    assert ACCOUNT_NUMBER in text, "landlord mode must keep the account number"
    assert NINO not in text, "NINO is never ownership proof — still redacted"
    assert "1200.00" in text, "the credit row itself must survive"
