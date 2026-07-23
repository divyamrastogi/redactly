"""End-to-end tests for the Redactly redesign + new pricing.

These drive the exact HTTP flow the redesigned frontend uses — the ``/redact``
endpoint the tool card POSTs to, the ``/download`` route the result card links
to, the credit paywall, and the ``/buy`` checkout redirect — with a synthetic
statement PDF (no real statement ever enters the repo). Stripe is faked, so no
network call is made.
"""
import io
import os
import sys
import types

import fitz
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import app as app_module
import payments

app = app_module.app

# Column positions mirror the synthetic builder in test_bank_generic.
X_DATE, X_DESC, X_PAID_OUT, X_PAID_IN, X_BAL = 50, 110, 340, 430, 510


def _build_statement_bytes():
    """A synthetic 1-page UK bank statement. 'Landlord Rent' is the keep row."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), "Sort Code 12-34-56")
    page.insert_text((50, 80), "Jane Smith")
    for text, x in [("Date", X_DATE), ("Description", X_DESC),
                    ("Paid out", X_PAID_OUT), ("Paid in", X_PAID_IN),
                    ("Balance", X_BAL)]:
        page.insert_text((x, 110), text)
    rows = [
        ("01 May", "Payroll",        None,      "2000.00", "2500.00"),
        ("03 May", "Tesco",          "45.20",   None,      "2454.80"),
        ("05 May", "Pret A Manger",  "8.75",    None,      "2446.05"),
        ("07 May", "Landlord Rent",  "1200.00", None,      "1246.05"),
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
    data = doc.tobytes()
    doc.close()
    return data


def _upload(client, keywords="Rent", mode="custom", provider="auto"):
    return client.post("/redact", data={
        "pdf": (io.BytesIO(_build_statement_bytes()), "statement.pdf"),
        "keywords": keywords,
        "provider": provider,
        "mode": mode,
    }, content_type="multipart/form-data")


def _disable_payments(monkeypatch):
    for var in ("PAYMENTS_ENABLED", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                "STRIPE_PRICE_SINGLE", "STRIPE_PRICE_PACK10"):
        monkeypatch.delenv(var, raising=False)


def _enable_payments(monkeypatch):
    monkeypatch.setenv("PAYMENTS_ENABLED", "1")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fake")
    monkeypatch.setenv("STRIPE_PRICE_SINGLE", "price_single_fake")
    monkeypatch.setenv("STRIPE_PRICE_PACK10", "price_pack10_fake")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-secure")


# --- (1) full redact → download round trip ---------------------------------

def test_redact_then_download_round_trip(monkeypatch):
    """The tool card's exact POST succeeds, and the linked download streams a
    real redacted PDF whose kept row survives while other rows are destroyed.

    ``/download`` deletes the temp output after streaming, so nothing is left in
    the tree — the same self-cleaning path production uses."""
    _disable_payments(monkeypatch)
    client = app.test_client()

    resp = _upload(client, keywords="Rent")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["download_url"].startswith("/download/")
    assert body["filename"].lower().endswith(".pdf")
    assert body["kept_count"] >= 1

    # Payments off → no per-visitor credit accounting is exposed.
    assert "credits_remaining" not in body

    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    out = dl.get_data()
    assert out[:4] == b"%PDF"

    # The redacted output preserves the kept row and destroys the hidden ones.
    doc = fitz.open(stream=out, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    assert "Rent" in text
    assert "Netflix" not in text
    assert "Tesco" not in text


# --- (2) new pricing + brand surfaced on the homepage ----------------------

def test_homepage_has_new_pricing_and_tool_wiring(monkeypatch):
    _disable_payments(monkeypatch)
    body = app.test_client().get("/").get_data(as_text=True)
    # Redactly brand + new price points.
    assert "Redactly" in body
    assert "99p" in body
    assert "£7.99" in body
    assert "Pack of 10" in body
    # The paywall JS points at the renamed pack.
    assert "pack=pack10" in body
    assert "pack=pack5" not in body
    # Every element id the tool-card JS depends on is present.
    for el in ("pdf-input", "drop-zone", "keywords", "provider", "mode-group",
               "enhanced_privacy", "submit-btn", "results-section", "results",
               "credits-badge"):
        assert f'id="{el}"' in body, f"missing #{el}"


# --- (3) paywall after the free document, with new pack -------------------

def test_free_then_paywall_with_pack10(monkeypatch):
    _enable_payments(monkeypatch)
    client = app.test_client()

    first = _upload(client, keywords="Rent")
    assert first.status_code == 200  # first document free
    # The response tells the client the new balance (was 1 free, now 0).
    assert first.get_json()["credits_remaining"] == 0
    # Consume the output via /download so nothing is left in the tree.
    client.get(first.get_json()["download_url"])

    second = _upload(client, keywords="Rent")
    assert second.status_code == 402
    err = second.get_json()
    assert err["error"] == "no_credits"
    assert err["buy_url"] == "/buy"


def test_buy_pack10_redirects_and_pack5_is_gone(monkeypatch):
    _enable_payments(monkeypatch)
    fake = types.SimpleNamespace(url="https://checkout.stripe.test/session_abc")
    monkeypatch.setattr(payments, "create_checkout_session", lambda pack: fake)
    client = app.test_client()

    ok = client.get("/buy?pack=pack10")
    assert ok.status_code == 302
    assert ok.headers["Location"] == "https://checkout.stripe.test/session_abc"

    single = client.get("/buy?pack=single")
    assert single.status_code == 302

    gone = client.get("/buy?pack=pack5")
    assert gone.status_code == 400  # the old pack no longer exists


def test_credit_counter_reflects_balance(monkeypatch):
    """The tool card shows the visitor how many redactions they have left."""
    _enable_payments(monkeypatch)
    client = app.test_client()

    # Brand-new visitor (no cookie): the badge markets the free document.
    fresh = client.get("/").get_data(as_text=True)
    assert 'id="credits-badge"' in fresh
    assert "First one free" in fresh

    # A visitor holding a pack sees the live count (plural).
    client.set_cookie(payments.COOKIE_NAME,
                      payments._serializer().dumps({"credits": 10}))
    ten = client.get("/").get_data(as_text=True)
    assert "10 documents left" in ten

    # Down to the last one: singular wording, no stray "s".
    client.set_cookie(payments.COOKIE_NAME,
                      payments._serializer().dumps({"credits": 1}))
    assert "1 document left" in client.get("/").get_data(as_text=True)

    # Exhausted: shows zero and gets the low (amber) styling hook.
    client.set_cookie(payments.COOKIE_NAME,
                      payments._serializer().dumps({"credits": 0}))
    zero = client.get("/").get_data(as_text=True)
    assert "0 documents left" in zero
    assert "credits-ind card-credits low" in zero  # amber low-balance styling
    assert "credits-ind header-credits low" in zero  # header chip mirrors it


def test_counter_absent_when_payments_off(monkeypatch):
    """With payments off the tool is free/unlimited — no misleading counter."""
    _disable_payments(monkeypatch)
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'data-payments="off"' in body
    assert "First one free" in body
    assert "documents left" not in body


def test_pack10_grants_ten_credits():
    """The economic change: a pack purchase grants ten credits, not five."""
    session = types.SimpleNamespace(metadata={"pack": "pack10"})
    assert payments.credits_for_session(session) == 10
    assert payments.PACK_CREDITS["pack10"] == 10
    assert "pack5" not in payments.PACK_CREDITS
