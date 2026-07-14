"""Tests for provider auto-detection (Phase 0, Task 0.1).

Sample statements are synthesized with PyMuPDF (fitz) inside fixtures — no real
statements ever live in this repo.
"""
import os
import sys

import fitz
import pytest

# Make the repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from provider_config import detect_provider


def _write_pdf(path, body_text):
    """Build a tiny 1-page PDF whose page text is `body_text`."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), body_text)
    doc.save(str(path))
    doc.close()


# --- Direct unit tests on detect_provider (no PDF needed) --------------------

@pytest.mark.parametrize("text", ["American Express", "amex gold card", "My AMEX statement"])
def test_detects_amex(text):
    assert detect_provider(text) == 'amex_uk'


@pytest.mark.parametrize("text", ["Barclaycard", "barclays credit card", "BARCLAYCARD"])
def test_detects_barclaycard(text):
    assert detect_provider(text) == 'barclaycard'


@pytest.mark.parametrize("text", ["Monzo Bank", "NatWest", "", "Totally unrelated text"])
def test_unknown_returns_none(text):
    assert detect_provider(text) is None


# --- PDF-backed tests (honour the project testing convention) ----------------

def test_detect_amex_from_synthetic_pdf(tmp_path):
    p = tmp_path / "amex.pdf"
    _write_pdf(p, "American Express\nStatement\nJan 15  TFL Travel   2.40")
    text = fitz.open(str(p))[0].get_text()
    assert detect_provider(text) == 'amex_uk'


def test_detect_barclaycard_from_synthetic_pdf(tmp_path):
    p = tmp_path / "barclaycard.pdf"
    _write_pdf(p, "Barclaycard\n15 Jan  Sainsbury's   12.50")
    text = fitz.open(str(p))[0].get_text()
    assert detect_provider(text) == 'barclaycard'


def test_detect_unknown_from_synthetic_pdf(tmp_path):
    p = tmp_path / "monzo.pdf"
    _write_pdf(p, "Monzo Bank\nStatement\n01 May  Coffee   3.20")
    text = fitz.open(str(p))[0].get_text()
    assert detect_provider(text) is None
