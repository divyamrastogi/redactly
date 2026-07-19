"""End-to-end regression tests against REAL bank statements (local only).

The PDFs live in ``tests/e2e_statements/`` (or ``$E2E_STATEMENTS_DIR``), which
is gitignored — real statements must NEVER enter the repository. When the
directory is empty or missing (e.g. CI), every test here is skipped; the
synthetic-fixture suites still run everywhere.

How it works
------------
For every ``<name>.pdf`` in the directory:

1. Hard invariants are asserted (provider detected, no-match redaction keeps
   zero rows, page count preserved, output text is a subset of input text).
2. A behaviour snapshot is captured: detected provider, kept-row counts in
   both modes, and per-page character/amount-span counts of the redacted
   output. On first run the snapshot is written to ``<name>.snapshot.json``
   next to the PDF; on later runs any drift fails the test.

To intentionally accept a behaviour change, delete the stale
``*.snapshot.json`` and rerun pytest to regenerate it.
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import fitz

from provider_config import detect_provider
from redact_bank_generic import redact_bank_generic

STATEMENTS_DIR = os.environ.get(
    'E2E_STATEMENTS_DIR',
    os.path.join(os.path.dirname(__file__), 'e2e_statements'),
)

AMOUNT_RE = re.compile(r'^-?£?[\d,]+\.\d{2}$')


def _statement_paths():
    if not os.path.isdir(STATEMENTS_DIR):
        return []
    return sorted(
        os.path.join(STATEMENTS_DIR, f)
        for f in os.listdir(STATEMENTS_DIR)
        if f.lower().endswith('.pdf')
    )


def _page_metrics(doc):
    """Per-page counts that fingerprint redaction behaviour without recording
    any statement content."""
    metrics = []
    for page in doc:
        spans = [
            s['text'].strip()
            for b in page.get_text('dict')['blocks']
            for l in b.get('lines', [])
            for s in l['spans']
            if s['text'].strip()
        ]
        metrics.append({
            'chars': len(' '.join(' '.join(spans).split())),
            'spans': len(spans),
            'amount_spans': sum(1 for t in spans if AMOUNT_RE.match(t)),
        })
    return metrics


def _words(text):
    return set(text.split())


PDFS = _statement_paths()


@pytest.mark.skipif(not PDFS, reason='no real statements in tests/e2e_statements/')
@pytest.mark.parametrize('pdf_path', PDFS, ids=[os.path.basename(p) for p in PDFS])
def test_real_statement_regression(pdf_path, tmp_path):
    name = os.path.splitext(os.path.basename(pdf_path))[0]
    original = fitz.open(pdf_path)
    n_pages = len(original)
    provider = detect_provider(original[0].get_text())

    # --- Hard invariants -----------------------------------------------------
    assert provider is not None, 'real statement no longer auto-detected'

    out_custom = tmp_path / f'{name}_custom.pdf'
    _, _, kept_custom = redact_bank_generic(
        pdf_path, str(out_custom), ['zzz-e2e-never-matches'])
    assert kept_custom == [], 'no-match redaction kept transactions'

    out_landlord = tmp_path / f'{name}_landlord.pdf'
    _, _, kept_landlord = redact_bank_generic(
        pdf_path, str(out_landlord), [], keep_credits=True)

    redacted = fitz.open(str(out_custom))
    assert len(redacted) == n_pages

    # Redaction only ever REMOVES text: every word in the output must have
    # existed in the input (per page).
    for i in range(n_pages):
        extra = _words(redacted[i].get_text()) - _words(original[i].get_text())
        assert not extra, f'page {i + 1} gained words it never had: {sorted(extra)[:5]}'

    # --- Behaviour snapshot ----------------------------------------------------
    snapshot = {
        'provider': provider,
        'pages': n_pages,
        'kept_custom_no_match': len(kept_custom),
        'kept_landlord': len(kept_landlord),
        'redacted_page_metrics': _page_metrics(redacted),
        'landlord_page_metrics': _page_metrics(fitz.open(str(out_landlord))),
    }

    snap_path = os.path.join(STATEMENTS_DIR, f'{name}.snapshot.json')
    if not os.path.exists(snap_path):
        with open(snap_path, 'w') as f:
            json.dump(snapshot, f, indent=2)
        pytest.skip(f'snapshot created: {snap_path} — rerun to verify')

    with open(snap_path) as f:
        expected = json.load(f)
    assert snapshot == expected, (
        'behaviour changed on a real statement — inspect the diff; if the '
        f'change is intentional, delete {snap_path} and rerun to regenerate'
    )
