"""Tests for the payments scaffold (Phase 4, Task 4.1).

No network: the Stripe wrappers in ``payments`` are monkeypatched, so no real
Stripe call is ever made. The absolute rule under test is that with payments OFF
(the default / today's production state) the app is free, unlimited, and touches
no cookie — and when ON it gates redaction on a signed-credit cookie, grants
credits exactly once per paid session, and gives every new visitor one free
document.
"""
import io
import os
import sys
import types

import pytest

# Make the repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import payments
import app as app_module
from app import app


# --- helpers ----------------------------------------------------------------

_STRIPE_VARS = (
    'PAYMENTS_ENABLED',
    'STRIPE_SECRET_KEY',
    'STRIPE_WEBHOOK_SECRET',
    'STRIPE_PRICE_SINGLE',
    'STRIPE_PRICE_PACK5',
)


def _enable_payments(monkeypatch):
    """Turn payments ON for one test with fake-but-complete Stripe env."""
    monkeypatch.setenv('PAYMENTS_ENABLED', '1')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_fake')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_fake')
    monkeypatch.setenv('STRIPE_PRICE_SINGLE', 'price_single_fake')
    monkeypatch.setenv('STRIPE_PRICE_PACK5', 'price_pack5_fake')
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key-secure')


def _disable_payments(monkeypatch):
    """Ensure payments are OFF (today's production default)."""
    for var in _STRIPE_VARS:
        monkeypatch.delenv(var, raising=False)


def _pdf_bytes():
    """Tiny in-memory file placeholder — never opened (process_single_file is
    monkeypatched in the route tests), just needs to be a file part."""
    return io.BytesIO(b'%PDF-1.4\nplaceholder\n%%EOF')


def _cookie_value(resp, name):
    """Extract the value of cookie ``name`` from a response's Set-Cookie headers,
    or None if absent."""
    for header in resp.headers.getlist('Set-Cookie'):
        if header.startswith(name + '='):
            return header.split(';', 1)[0][len(name) + 1:]
    return None


def _post_redact(client, cookie=None):
    """POST a minimal /redact request, optionally with a pre-set credits cookie.

    Uses client.set_cookie — Werkzeug's test client manages its own cookie jar
    and ignores a hand-built ``Cookie:`` header.
    """
    if cookie:
        client.set_cookie(payments.COOKIE_NAME, cookie)
    return client.post('/redact', data={
        'pdf': (_pdf_bytes(), 'stmt.pdf'),
        'keywords': 'rent',
        'provider': 'auto',
    }, content_type='multipart/form-data')


# --- (1) flag OFF → free + no cookie ---------------------------------------

def test_payments_off_redact_free_and_sets_no_cookie(monkeypatch):
    """With PAYMENTS_ENABLED unset, /redact works with no cookie and writes none."""
    _disable_payments(monkeypatch)
    assert payments.payments_enabled() is False

    # Prove the free path never imports stripe: stripe is imported lazily inside
    # the enabled-only wrappers, so even a missing stripe package cannot break the
    # disabled (production) path. Clear any cached import for a clean check.
    sys.modules.pop('stripe', None)

    monkeypatch.setattr(app_module, 'update_usage_counter', lambda: 0)
    monkeypatch.setattr(
        app_module, 'process_single_file',
        lambda *a, **k: ('redacted_free.pdf', 1.50, 1, True, 'barclaycard', False))

    client = app.test_client()
    resp = _post_redact(client)  # no cookie sent

    assert resp.status_code == 200
    assert resp.get_json()['filename'] == 'redacted_free.pdf'
    # No credit cookie is read or written while payments are off.
    assert _cookie_value(resp, payments.COOKIE_NAME) is None
    # The disabled path must not touch the stripe package at all.
    assert 'stripe' not in sys.modules


# --- (2) cookie serializer round-trip + tamper rejection -------------------

def test_cookie_roundtrip_and_tamper_rejection(monkeypatch):
    """Signed credit cookies round-trip; a tampered cookie is rejected, not
    trusted, and never raises."""
    monkeypatch.setenv('SECRET_KEY', 'unit-test-secret')

    token = payments._serializer().dumps({'credits': 5})
    good_req = types.SimpleNamespace(cookies={payments.COOKIE_NAME: token})
    assert payments.get_credits(good_req) == 5

    # Flip the final char of a valid token → signature no longer matches.
    tampered = token[:-1] + ('A' if token[-1] != 'A' else 'B')
    bad_req = types.SimpleNamespace(cookies={payments.COOKIE_NAME: tampered})
    assert payments.get_credits(bad_req) is None

    # Absent cookie → None (treated as a fresh visitor upstream).
    assert payments.get_credits(types.SimpleNamespace(cookies={})) is None


# --- (3) flag ON + 0-credit cookie → 402 with buy_url ----------------------

def test_zero_credits_returns_402_before_processing(monkeypatch):
    """A zero-credit cookie with payments on short-circuits to 402 without ever
    calling the redaction engine."""
    _enable_payments(monkeypatch)
    monkeypatch.setattr(app_module, 'update_usage_counter', lambda: 0)

    called = []
    def _should_not_run(*a, **k):
        called.append(True)
        return ('x.pdf', 0, 0, True, 'barclaycard', False)
    monkeypatch.setattr(app_module, 'process_single_file', _should_not_run)

    zero_token = payments._serializer().dumps({'credits': 0})
    client = app.test_client()
    resp = _post_redact(client, cookie=zero_token)

    assert resp.status_code == 402
    body = resp.get_json()
    assert body['error'] == 'no_credits'
    assert body['buy_url'] == '/buy'
    assert called == []  # proved we never processed the document


# --- (4) /paid grants once; second hit with same session does not re-grant --

def test_paid_grants_credits_once_only(monkeypatch, tmp_path):
    """A paid Checkout Session grants credits exactly once; a second /paid hit
    for the same session_id is a no-op (consumed-session guard)."""
    _enable_payments(monkeypatch)
    # Keep the consumed-sessions ledger in the temp dir (out of the repo).
    monkeypatch.setattr(payments, 'CONSUMED_SESSIONS_FILE', str(tmp_path / 'consumed.txt'))

    fake_session = types.SimpleNamespace(
        payment_status='paid', metadata={'pack': 'pack5'})
    monkeypatch.setattr(payments, 'retrieve_session', lambda sid: fake_session)

    client = app.test_client()

    r1 = client.get('/paid?session_id=sess_123')
    assert r1.status_code == 302
    assert 'pay=success' in r1.headers['Location']
    token1 = _cookie_value(r1, payments.COOKIE_NAME)
    assert token1 is not None
    assert payments._serializer().loads(token1)['credits'] == 5  # 0 + pack5

    # Same session id again → already consumed, no second grant.
    r2 = client.get('/paid?session_id=sess_123')
    assert r2.status_code == 302
    assert 'pay=already' in r2.headers['Location']
    assert _cookie_value(r2, payments.COOKIE_NAME) is None

    # The ledger records the session exactly once.
    consumed = (tmp_path / 'consumed.txt').read_text()
    assert consumed.count('sess_123') == 1


# --- (5) fresh visitor with payments on gets 1 free credit -----------------

def test_fresh_visitor_gets_one_free_credit(monkeypatch):
    """With payments on, a visitor with no cookie is given 1 free credit and
    /redact succeeds, spending it down to 0."""
    _enable_payments(monkeypatch)
    monkeypatch.setattr(app_module, 'update_usage_counter', lambda: 0)
    monkeypatch.setattr(
        app_module, 'process_single_file',
        lambda *a, **k: ('redacted_first.pdf', 2.00, 1, True, 'barclaycard', False))

    client = app.test_client()
    resp = _post_redact(client)  # no cookie → fresh visitor

    assert resp.status_code == 200
    assert resp.get_json()['filename'] == 'redacted_first.pdf'
    # 1 free credit granted then spent (−1) → cookie now holds 0.
    token = _cookie_value(resp, payments.COOKIE_NAME)
    assert token is not None
    assert payments._serializer().loads(token)['credits'] == 0
