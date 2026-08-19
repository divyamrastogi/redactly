"""End-to-end tests for the Redactly redesign (free / open-source edition).

These drive the exact HTTP flow the redesigned frontend uses — the ``/redact``
endpoint the tool card POSTs to and the ``/download`` route the result card
links to — with a synthetic statement PDF (no real statement ever enters the
repo). No network calls are made.
"""
import io
import os
import sys

import fitz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir)))

import app as app_module

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


# --- (1) full redact → download round trip ---------------------------------

def test_redact_then_download_round_trip():
    """The tool card's exact POST succeeds, and the linked download streams a
    real redacted PDF whose kept row survives while other rows are destroyed.

    ``/download`` deletes the temp output after streaming, so nothing is left in
    the tree — the same self-cleaning path production uses."""
    client = app.test_client()

    resp = _upload(client, keywords="Rent")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["download_url"].startswith("/download/")
    assert body["filename"].lower().endswith(".pdf")
    assert body["kept_count"] >= 1

    # Free tool: no per-visitor credit accounting exists.
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


# --- (2) free/open-source messaging + tool wiring on the homepage -----------

def test_homepage_is_free_with_no_paywall():
    body = app.test_client().get("/").get_data(as_text=True)
    # Redactly brand + free messaging.
    assert "Redactly" in body
    assert "Free" in body
    assert "open source" in body
    # No paid-product leftovers: no pricing, no paywall, no buy links.
    for absent in ("99p", "£7.99", "pack=pack10", "/buy", "no_credits",
                   "showPaywall", "updateCreditsBadge", "credits-badge",
                   "documents left", "First one free"):
        assert absent not in body, f"unexpected {absent!r} on homepage"
    # Every element id the tool-card JS depends on is present.
    for el in ("pdf-input", "drop-zone", "keywords", "provider", "mode-group",
               "enhanced_privacy", "submit-btn", "results-section", "results",
               "free-badge"):
        assert f'id="{el}"' in body, f"missing #{el}"


# --- (3) retired payment routes are gone ------------------------------------

def test_payment_routes_are_gone():
    client = app.test_client()
    for path in ("/buy?pack=single", "/buy?pack=pack10", "/stripe-webhook"):
        assert client.get(path).status_code == 404, path
