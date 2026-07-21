"""Privacy guard for the homepage analytics layer.

Every analytics event is routed through the ``track()`` chokepoint, and any
monetary total is collapsed into a coarse band by ``bandTotal()`` before it
reaches Google Analytics. These tests pin that contract: the wrappers must
exist on the homepage, and — above all — no ``gtag(...)`` call may ever carry an
exact total. Statement content, keywords, filenames, and exact amounts must
never reach a tracker.

Uses Flask's test_client against the rendered homepage — no network, no real
statements. The sandbox that produced this change cannot run Python, so the
assertions reason only about the rendered HTML string.
"""
import os
import re
import sys

import pytest

# Make the repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from app import app


@pytest.fixture()
def html():
    """Render the homepage once and return it as a string."""
    app.config['TESTING'] = True
    with app.test_client() as c:
        resp = c.get('/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_homepage_exposes_track_wrapper(html):
    # track() is the single chokepoint every analytics event must pass through.
    assert 'function track(' in html


def test_homepage_exposes_bandtotal_wrapper(html):
    # bandTotal() collapses an exact £ amount into a coarse band.
    assert 'function bandTotal(' in html


def test_no_gtag_call_passes_an_exact_total(html):
    # The heart of the privacy rule: no gtag(...) call may carry an exact total.
    #
    # `total_band` (an underscore right after 'total') is explicitly ALLOWED —
    # only a bare 'total' followed by a non-underscore character is rejected
    # (e.g. ``data.total)`` or ``total:``). ``[^)]*`` keeps the match inside one
    # gtag() call, so a 'total' that merely appears elsewhere on the page (in a
    # track() call, in a comment, in bandTotal's body) never trips this.
    forbidden = re.compile(r'gtag\([^)]*total[^_]')
    match = forbidden.search(html)
    assert match is None, (
        f'a gtag() call leaks an exact total: {match.group(0)!r}'
    )


def test_every_event_is_routed_through_track(html):
    # The only gtag('event', ...) that may remain is the single call inside
    # track() itself — every direct event call has been replaced.
    assert html.count("gtag('event'") == 1, (
        "expected exactly one gtag('event' call (the track() chokepoint)"
    )


def test_redact_success_sends_banded_total(html):
    # redact_success is routed via track() and carries total_band (a band),
    # never the exact amount. The non-greedy [\s\S]*? spans the multi-line call.
    assert re.search(r"track\(\s*'redact_success'[\s\S]*?total_band", html), (
        'redact_success must send total_band via track()'
    )


def test_custom_request_card_is_anchorable(html):
    # The custom-request card carries an id so it can be deep-linked.
    assert 'id="custom-request"' in html


def test_bandtotal_emits_documented_bands(html):
    # The first two bands are part of the spec; confirm they're present in the
    # wrapper body (guards against the band literals being dropped).
    assert "'0-10'" in html
    assert "'11-50'" in html
    # Invalid / missing input must degrade to a non-numeric sentinel, not 0.
    assert "'unknown'" in html


def test_alias_host_gets_301_to_canonical():
    from app import app
    client = app.test_client()
    resp = client.get('/guides', headers={'Host': 'redactpdf.javascriptbit.com'})
    assert resp.status_code == 301
    assert resp.headers['Location'].startswith('https://redact.javascriptbit.com/guides')


def test_canonical_and_local_hosts_not_redirected():
    from app import app
    client = app.test_client()
    assert client.get('/robots.txt', headers={'Host': 'redact.javascriptbit.com'}).status_code == 200
    assert client.get('/robots.txt').status_code == 200  # localhost default
