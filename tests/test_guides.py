"""Tests for Phase 1 SEO guide pages, sitemap, robots, and homepage copy fixes.

Uses Flask's test_client — no network, no real statements. Sample data is the
GUIDES dict from guides.py plus the rendered homepage.
"""
import os
import sys

import pytest

# Make the repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from app import app
from guides import GUIDES


@pytest.fixture()
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


# --- Guide pages -----------------------------------------------------------

def test_guides_dict_has_seven_entries():
    assert len(GUIDES) == 7


def test_each_guide_has_required_fields():
    for slug, guide in GUIDES.items():
        assert guide['title']
        assert guide['meta_description']
        assert guide['html_body']
        # Every guide ends with a single CTA pointing home.
        assert 'href="/"' in guide['html_body']


def test_every_guide_returns_200_and_contains_title(client):
    for slug, guide in GUIDES.items():
        resp = client.get('/guides/' + slug)
        assert resp.status_code == 200, slug
        body = resp.get_data(as_text=True)
        assert guide['title'] in body
        # The honest mortgage/visa caveat must be present on every guide.
        assert 'visa applications generally require unredacted statements' in body


def test_unknown_guide_returns_404(client):
    resp = client.get('/guides/this-slug-does-not-exist')
    assert resp.status_code == 404


def test_guide_page_reuses_site_shell(client):
    # The shared header/footer/theme must be present on a guide page.
    body = client.get('/guides/why-black-boxes-fail-pdf-redaction').get_data(as_text=True)
    assert 'logo-name' in body          # site header
    assert 'site-footer' in body        # site footer
    assert 'Guides' in body             # footer Guides link
    assert 'theme-toggle' in body       # theme toggle button


# --- Sitemap & robots ------------------------------------------------------

def test_sitemap_contains_homepage_and_all_guide_urls(client):
    resp = client.get('/sitemap.xml')
    assert resp.status_code == 200
    assert 'xml' in resp.content_type
    body = resp.get_data(as_text=True)
    assert '<urlset' in body
    # Homepage + all four guides.
    assert '/</loc>' in body
    for slug in GUIDES:
        assert ('/guides/' + slug) in body


def test_robots_txt_is_200_and_points_at_sitemap(client):
    resp = client.get('/robots.txt')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'User-agent: *' in body
    assert 'Allow: /' in body
    assert 'Sitemap:' in body
    assert 'sitemap.xml' in body


# --- Homepage false-claim fixes (Task 1.1) ---------------------------------

def test_homepage_no_false_claims(client):
    resp = client.get('/')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Removed false claims.
    assert 'Runs locally' not in body
    assert 'Visa' not in body
    assert 'Mastercard' not in body


def test_homepage_has_repositioned_copy(client):
    body = client.get('/').get_data(as_text=True)
    assert 'True redaction · Files deleted after download' in body
    assert 'Share your statement.' in body
    assert 'Not your whole life.' in body
    # Trust section headings present.
    assert 'Nothing is kept' in body
    assert 'No account needed' in body


# --- LLM/AI discoverability ---------------------------------------------------

def test_llms_txt_serves_summary_with_all_guides():
    client = app.test_client()
    resp = client.get('/llms.txt')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '# Redact Statements' in body
    assert 'true' in body.lower() and 'redaction' in body.lower()
    for slug in GUIDES:
        assert f'/guides/{slug}' in body
    # Honest limits stated for AI answers too.
    assert 'visa' in body.lower() and 'mortgage' in body.lower()


def test_robots_allows_ai_crawlers():
    body = app.test_client().get('/robots.txt').get_data(as_text=True)
    for bot in ('GPTBot', 'ClaudeBot', 'PerplexityBot', 'Google-Extended'):
        assert f'User-agent: {bot}' in body


def test_structured_data_on_homepage_and_guides():
    client = app.test_client()
    home = client.get('/').get_data(as_text=True)
    assert '"@type": "WebApplication"' in home
    guide = client.get('/guides/do-landlords-accept-redacted-bank-statements').get_data(as_text=True)
    assert '"@type": "Article"' in guide


# --- Technical SEO (canonical, social, headers, sitemap) ----------------------

def test_canonical_and_social_tags():
    client = app.test_client()
    home = client.get('/').get_data(as_text=True)
    assert '<link rel="canonical" href="https://pdf-redact.onrender.com/">' in home or 'rel="canonical"' in home
    assert 'property="og:title"' in home and 'name="twitter:card"' in home
    assert 'rel="icon"' in home
    guide = client.get('/guides/redact-hsbc-statement').get_data(as_text=True)
    assert '/guides/redact-hsbc-statement">' in guide.split('rel="canonical"')[1][:120]
    assert '<meta property="og:type" content="article">' in guide


def test_security_headers_present():
    resp = app.test_client().get('/robots.txt')
    assert 'max-age=31536000' in resp.headers.get('Strict-Transport-Security', '')
    assert resp.headers.get('X-Content-Type-Options') == 'nosniff'


def test_sitemap_has_lastmod_and_guides_index():
    body = app.test_client().get('/sitemap.xml').get_data(as_text=True)
    assert body.count('<lastmod>') == body.count('<loc>')
    assert '/guides</loc>' in body
