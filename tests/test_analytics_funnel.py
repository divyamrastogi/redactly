"""Funnel + Umami analytics layer.

Umami (cookieless, blocker-resilient) runs alongside GA4. Its tag is only
rendered when UMAMI_WEBSITE_ID is set, so forks and local runs stay untracked.
The shared ``track()`` chokepoint fans out to both trackers and lives in the
shared shell, so guide pages can report CTA clicks too.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from app import app

UMAMI_SRC = 'https://cloud.umami.is/script.js'


def _get(path, env=None):
    app.config['TESTING'] = True
    old = os.environ.pop('UMAMI_WEBSITE_ID', None)
    if env:
        os.environ['UMAMI_WEBSITE_ID'] = env
    try:
        with app.test_client() as c:
            resp = c.get(path)
    finally:
        os.environ.pop('UMAMI_WEBSITE_ID', None)
        if old is not None:
            os.environ['UMAMI_WEBSITE_ID'] = old
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_umami_tag_absent_without_env():
    html = _get('/')
    assert UMAMI_SRC not in html


def test_umami_tag_present_with_env_on_homepage_and_guides():
    for path in ('/', '/guides'):
        html = _get(path, env='abc-123')
        assert UMAMI_SRC in html, path
        assert 'data-website-id="abc-123"' in html, path


def test_umami_id_is_html_escaped():
    html = _get('/', env='"><script>alert(1)</script>')
    assert '<script>alert(1)</script>' not in html


def test_track_fans_out_to_umami():
    html = _get('/')
    # track() must forward to umami.track as well as gtag.
    body = re.search(r'function track\([\s\S]*?\n}', html).group(0)
    assert 'umami' in body
    assert "gtag('event'" in body


def test_track_available_on_guide_pages():
    html = _get('/guides')
    assert 'function track(' in html


def test_funnel_events_present_on_homepage():
    html = _get('/')
    for ev in ('file_selected', 'redact_clicked', 'download_clicked',
               'outbound_click', 'guide_cta_click'):
        assert f"track('{ev}'" in html, ev


def test_funnel_events_never_carry_filenames_or_keywords():
    html = _get('/')
    for m in re.finditer(r"track\('(file_selected|redact_clicked|download_clicked)'[^;]*", html):
        assert 'name' not in m.group(0), m.group(0)
        assert 'keywords' not in m.group(0), m.group(0)
