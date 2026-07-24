"""Tests for the /contact-sample endpoint — the "share a sample statement, earn
5 redactions" flow.

No network: ``app._notify_contact`` (which POSTs to the Brevo Edge Function) is
monkeypatched, so no email is ever sent. Payments are turned ON with fake Stripe
env because the bonus only grants when payments are enabled. The rules under
test: a readable statement from an UNSUPPORTED provider grants +5 exactly once
per visitor; a supported provider (AMEX/Barclaycard) or an unreadable/blank PDF
grants nothing; and the sample PDF is always forwarded as an email attachment,
never stored.
"""
import io
import os
import sys

import pytest

# Make the repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import payments
import app as app_module
from app import app


def _enable_payments(monkeypatch):
    monkeypatch.setenv('PAYMENTS_ENABLED', '1')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_fake')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_fake')
    monkeypatch.setenv('STRIPE_PRICE_SINGLE', 'price_single_fake')
    monkeypatch.setenv('STRIPE_PRICE_PACK10', 'price_pack10_fake')
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key-secure')


def _make_pdf(lines):
    """A tiny in-memory PDF whose text PyMuPDF can extract, for provider
    detection."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for ln in lines:
        page.insert_text((72, y), ln)
        y += 16
    data = doc.tobytes()
    doc.close()
    return data


_GENERIC = None
_AMEX = None


def _generic():
    global _GENERIC
    if _GENERIC is None:
        _GENERIC = _make_pdf([
            "MONZO BANK STATEMENT",
            "Sort code 12-34-56  Account 12345678",
            "Date        Description        Paid out   Paid in    Balance",
            "01 Jul 2026 TESCO STORES         45.20                954.80",
            "03 Jul 2026 SALARY ACME LTD                2100.00   3054.80",
            "05 Jul 2026 RENT                 900.00               2154.80",
            "Genuine-looking statement with sort code, paid in and paid out columns.",
            "Filler text to comfortably exceed the minimum readable-text threshold.",
        ])
    return _GENERIC


def _amex():
    global _AMEX
    if _AMEX is None:
        _AMEX = _make_pdf([
            "American Express",
            "Statement of Account — Card ending 1009",
            "01 Jul 2026 WAITROSE        52.10",
            "04 Jul 2026 SHELL PETROL    61.00",
            "An American Express statement, a provider we already fully support.",
            "Padding padding padding padding padding padding padding padding.",
        ])
    return _AMEX


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    """Capture the outbound notification instead of hitting Brevo."""
    captured = {}

    def fake_notify(record, attachment=None):
        captured['record'] = record
        captured['attachment'] = attachment
        return True

    monkeypatch.setattr(app_module, '_notify_contact', fake_notify)
    return captured


def _post(client, pdf_bytes=None, name="Jane", email="jane@example.com",
          details="Please support my bank."):
    data = {"name": name, "email": email,
            "project_type": "A bank we don't support yet", "details": details}
    if pdf_bytes is not None:
        data["sample"] = (io.BytesIO(pdf_bytes), "statement.pdf")
    return client.post("/contact-sample", data=data,
                       content_type="multipart/form-data")


def test_generic_statement_grants_bonus_once(monkeypatch, _no_email):
    _enable_payments(monkeypatch)
    client = app.test_client()

    r = _post(client, _generic())
    j = r.get_json()
    assert r.status_code == 200
    assert j['rewarded'] is True
    assert j['reason'] == 'granted'
    assert j['bonus_credits'] == payments.SAMPLE_BONUS_CREDITS
    # New visitor: 1 free + 5 bonus.
    assert j['credits_remaining'] == payments.FREE_CREDITS + payments.SAMPLE_BONUS_CREDITS
    # The sample was forwarded as an attachment, never stored.
    assert _no_email['attachment'] and _no_email['attachment']['content']
    assert 'detected: generic_bank_uk' in _no_email['record']['details']

    # Same visitor (cookie carried by the client) cannot claim again.
    r2 = _post(client, _generic())
    j2 = r2.get_json()
    assert j2['rewarded'] is False
    assert j2['reason'] == 'already_claimed'


def test_supported_provider_gets_no_bonus(monkeypatch, _no_email):
    _enable_payments(monkeypatch)
    client = app.test_client()
    r = _post(client, _amex())
    j = r.get_json()
    assert j['rewarded'] is False
    assert j['reason'] == 'already_supported'
    # Still emailed — a supported-provider layout variant may still be useful.
    assert _no_email['attachment'] is not None


def test_blank_pdf_is_not_rewarded(monkeypatch, _no_email):
    _enable_payments(monkeypatch)
    client = app.test_client()
    r = _post(client, _make_pdf(["hi"]))
    j = r.get_json()
    assert j['rewarded'] is False
    assert j['reason'] == 'unreadable'


def test_non_pdf_is_rejected(monkeypatch, _no_email):
    _enable_payments(monkeypatch)
    client = app.test_client()
    r = _post(client, b"this is definitely not a pdf")
    assert r.status_code == 400


def test_missing_fields_rejected(monkeypatch, _no_email):
    _enable_payments(monkeypatch)
    client = app.test_client()
    r = _post(client, _generic(), name="")
    assert r.status_code == 400


def test_text_only_submission_is_emailed_without_bonus(monkeypatch, _no_email):
    _enable_payments(monkeypatch)
    client = app.test_client()
    r = _post(client, None)  # no file
    j = r.get_json()
    assert j['ok'] is True
    assert j['reason'] == 'no_file'
    assert j['rewarded'] is False
