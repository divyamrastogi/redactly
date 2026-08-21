from flask import Flask, request, send_file, after_this_request, render_template_string, jsonify, abort, Response
import os
import re
import json
import base64
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from redact_transactions import redact_transactions
from redact_generic import redact_pdf_generic
from redact_financial_details import redact_barclaycard_with_privacy, redact_amex_with_privacy
from redact_barclaycard import redact_barclaycard
from redact_bank_generic import redact_bank_generic
from provider_config import get_all_providers, detect_provider

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Function to handle the usage counter
def update_usage_counter():
    counter_file = 'usage_counter.txt'
    try:
        if os.path.exists(counter_file):
            with open(counter_file, 'r+') as f:
                count = int(f.read() or '0') + 1
                f.seek(0)
                f.write(str(count))
                f.truncate()
        else:
            count = 1
            with open(counter_file, 'w') as f:
                f.write(str(count))
        return count
    except Exception as e:
        logger.error(f"Error updating usage counter: {str(e)}")
        return None


# Content changes ship via deploys, so process start date is an honest lastmod.
_DEPLOY_DATE = datetime.now(timezone.utc).strftime('%Y-%m-%d')


@app.context_processor
def _analytics_context():
    """Expose the Umami website id (if configured) to every template.

    Unset → no Umami tag is rendered, so forks and local runs stay untracked.
    """
    return {'umami_website_id': os.environ.get('UMAMI_WEBSITE_ID', '')}


@app.after_request
def _security_headers(response):
    response.headers.setdefault('Strict-Transport-Security',
                                'max-age=31536000; includeSubDomains')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    return response


@app.before_request
def _canonical_host_redirect():
    """301 alias hosts to the canonical domain (from BASE_URL).

    Only ever redirects KNOWN aliases — localhost, previews, and the canonical
    host itself pass through untouched.
    """
    alias_hosts = {'redactpdf.javascriptbit.com', 'pdf-redact.onrender.com'}
    if request.host in alias_hosts:
        base = os.environ.get('BASE_URL', 'https://redact.javascriptbit.com').rstrip('/')
        from flask import redirect as _redirect
        return _redirect(base + request.full_path.rstrip('?'), code=301)


def _iso_now():
    """ISO-8601 UTC timestamp for demand-signal log lines."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def log_unrecognized_upload():
    """Record that auto-detection could not identify a provider.

    Privacy: never log document content — only an ISO date + the literal 'unknown'.
    """
    try:
        with open('unrecognized_uploads.txt', 'a') as f:
            f.write(f"{_iso_now()},unknown\n")
    except Exception as e:
        logger.error(f"Error writing unrecognized_uploads.txt: {str(e)}")

# ─────────────────────────────────────────────────────────────────────────────
# Reusable site shell. The homepage and every guide page are assembled from the
# same segments (_SITE_OPEN … _SITE_END) so they share identical chrome — the
# header, footer, theme-toggle script, and the full light/dark theme CSS.
# Page-specific title/meta are injected via Jinja vars in _SITE_OPEN.
# ─────────────────────────────────────────────────────────────────────────────

_SITE_OPEN = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <meta name="description" content="{{ meta_description }}">
    {% if canonical %}<link rel="canonical" href="{{ canonical }}">{% endif %}
    <meta property="og:site_name" content="Redactly">
    <meta property="og:title" content="{{ title }}">
    <meta property="og:description" content="{{ meta_description }}">
    <meta property="og:type" content="{{ og_type or 'website' }}">
    {% if canonical %}<meta property="og:url" content="{{ canonical }}">{% endif %}
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="{{ title }}">
    <meta name="twitter:description" content="{{ meta_description }}">
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='9' fill='%234A9FD4'/%3E%3Crect x='7' y='9' width='18' height='3.4' rx='1.7' fill='white'/%3E%3Crect x='7' y='19.6' width='11' height='3.4' rx='1.7' fill='white' opacity='0.55'/%3E%3Crect x='7' y='14.3' width='16' height='3.9' rx='1.4' fill='black' opacity='0.82'/%3E%3C/svg%3E">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-SY9PXXMVD8"></script>
    <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', 'G-SY9PXXMVD8');
    </script>
    {% if umami_website_id %}<script defer src="https://cloud.umami.is/script.js" data-website-id="{{ umami_website_id }}"></script>{% endif %}
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        /* ── Redactly design tokens — Light (default) ── */
        :root {
            --bg:             #FAFBFC;
            --bg-card:        #FFFFFF;
            --bg-elevated:    #F2F6F9;
            --border:         #E5E7EB;
            --border-focus:   rgba(74,159,212,0.6);
            --text:           #1F2A37;
            --text-muted:     #5B6572;
            --text-faint:     #9AA4B2;
            --accent:         #4A9FD4;
            --accent-strong:  #3A8FC4;
            --accent-hover:   #3A8FC4;
            --accent-grad:    linear-gradient(135deg,#4A9FD4,#6BB5E0);
            --tint:           rgba(74,159,212,0.07);
            --glow:           0 4px 12px rgba(74,159,212,0.30);
            --success:        #059669;
            --success-bg:     rgba(5,150,105,0.08);
            --success-border: rgba(5,150,105,0.22);
            --warning:        #D97706;
            --warning-bg:     rgba(245,158,11,0.10);
            --warning-border: rgba(217,119,6,0.28);
            --error:          #DC2626;
            --error-bg:       rgba(220,38,38,0.06);
            --error-border:   rgba(220,38,38,0.22);
            --radius:         12px;
            --radius-lg:      18px;
            --shadow:         0 2px 8px rgba(0,0,0,0.04), 0 1px 4px rgba(0,0,0,0.02);
            --shadow-lg:      0 4px 16px rgba(0,0,0,0.08), 0 2px 8px rgba(0,0,0,0.04);
        }

        /* ── Dark tokens ── */
        .dark {
            --bg:             #0D1520;
            --bg-card:        #141F2C;
            --bg-elevated:    #1B2836;
            --border:         rgba(255,255,255,0.09);
            --border-focus:   rgba(107,181,224,0.7);
            --text:           #EAF1F8;
            --text-muted:     #9FB0C0;
            --text-faint:     #5F7183;
            --accent:         #6BB5E0;
            --accent-strong:  #4A9FD4;
            --accent-hover:   #8AC6EA;
            --tint:           rgba(74,159,212,0.14);
            --glow:           0 4px 14px rgba(74,159,212,0.35);
            --success:        #10B981;
            --success-bg:     rgba(16,185,129,0.12);
            --success-border: rgba(16,185,129,0.28);
            --warning:        #F59E0B;
            --warning-bg:     rgba(245,158,11,0.14);
            --warning-border: rgba(245,158,11,0.32);
            --error:          #F87171;
            --error-bg:       rgba(248,113,113,0.12);
            --error-border:   rgba(248,113,113,0.28);
            --shadow:         0 2px 10px rgba(0,0,0,0.45), 0 1px 4px rgba(0,0,0,0.35);
            --shadow-lg:      0 6px 24px rgba(0,0,0,0.55), 0 2px 8px rgba(0,0,0,0.4);
        }

        /* System dark when the visitor hasn't chosen a theme */
        @media (prefers-color-scheme: dark) {
            :root:not(.light) {
                --bg:             #0D1520;
                --bg-card:        #141F2C;
                --bg-elevated:    #1B2836;
                --border:         rgba(255,255,255,0.09);
                --border-focus:   rgba(107,181,224,0.7);
                --text:           #EAF1F8;
                --text-muted:     #9FB0C0;
                --text-faint:     #5F7183;
                --accent:         #6BB5E0;
                --accent-strong:  #4A9FD4;
                --accent-hover:   #8AC6EA;
                --tint:           rgba(74,159,212,0.14);
                --glow:           0 4px 14px rgba(74,159,212,0.35);
                --success:        #10B981;
                --success-bg:     rgba(16,185,129,0.12);
                --success-border: rgba(16,185,129,0.28);
                --warning:        #F59E0B;
                --warning-bg:     rgba(245,158,11,0.14);
                --warning-border: rgba(245,158,11,0.32);
                --error:          #F87171;
                --error-bg:       rgba(248,113,113,0.12);
                --error-border:   rgba(248,113,113,0.28);
                --shadow:         0 2px 10px rgba(0,0,0,0.45), 0 1px 4px rgba(0,0,0,0.35);
                --shadow-lg:      0 6px 24px rgba(0,0,0,0.55), 0 2px 8px rgba(0,0,0,0.4);
            }
        }

        html { scroll-behavior: smooth; }
        html, body {
            min-height: 100vh;
            background: var(--bg);
            color: var(--text);
            font-family: 'Poppins', system-ui, -apple-system, sans-serif;
            font-size: 14px;
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }
        a { color: var(--accent); text-decoration: none; }
        a:hover { color: var(--accent-strong); }

        /* ── Layout ── */
        .page-wrap { min-height: 100vh; display: flex; flex-direction: column; }
        .container { width: 100%; max-width: 1180px; margin: 0 auto; padding: 0 44px; }

        /* ── Header ── */
        .site-header { border-bottom: 1px solid var(--border); padding: 18px 0; }
        .header-inner { display: flex; align-items: center; justify-content: space-between; }
        .logo { display: flex; align-items: center; gap: 11px; text-decoration: none; }
        .logo-icon {
            width: 34px; height: 34px; border-radius: 10px;
            background: var(--accent-grad); box-shadow: var(--glow);
            display: flex; align-items: center; justify-content: center; flex-shrink: 0;
        }
        .logo-icon svg { width: 19px; height: 19px; color: #fff; }
        .logo-name { font-size: 17px; font-weight: 700; color: var(--text); letter-spacing: -0.02em; }
        .header-right { display: flex; align-items: center; gap: 22px; }
        .navlink { font-size: 13px; color: var(--text-muted); cursor: pointer; transition: color .15s; }
        .navlink:hover { color: var(--text); }
        .usage-badge {
            font-size: 12px; color: var(--text-muted); background: var(--bg-elevated);
            border: 1px solid var(--border); padding: 4px 11px; border-radius: 20px;
        }

        /* ── Theme toggle ── */
        .theme-toggle {
            width: 36px; height: 36px; display: flex; align-items: center; justify-content: center;
            background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 10px;
            cursor: pointer; color: var(--text-muted); transition: color .15s, border-color .15s;
        }
        .theme-toggle:hover { color: var(--accent); border-color: var(--accent); }
        .theme-toggle svg { width: 16px; height: 16px; }
        .icon-sun { display: none; }
        .icon-moon { display: block; }
        .dark .icon-sun { display: block; }
        .dark .icon-moon { display: none; }
        @media (prefers-color-scheme: dark) {
            :root:not(.light) .icon-sun { display: block; }
            :root:not(.light) .icon-moon { display: none; }
        }

        /* ── Hero (split) ── */
        .hero-split {
            display: grid; grid-template-columns: 1fr 1fr; gap: 44px;
            padding: 56px 0; align-items: start;
        }
        /* keep the hero copy optically centred against the taller tool card
           without re-centring (which would jump) when the card changes height */
        .hero-copy { padding-top: 24px; }
        .hero-badge {
            display: inline-flex; align-items: center; gap: 7px;
            font-size: 11px; font-weight: 600; letter-spacing: .07em; text-transform: uppercase;
            color: var(--accent); background: var(--tint); padding: 5px 13px; border-radius: 20px;
        }
        .hero-copy h1 {
            font-size: 44px; font-weight: 700; letter-spacing: -.03em; line-height: 1.1;
            margin: 20px 0 16px;
        }
        .hero-sub { font-size: 16px; color: var(--text-muted); line-height: 1.65; max-width: 440px; }
        .hero-cta-row { display: flex; gap: 14px; margin-top: 26px; align-items: center; flex-wrap: wrap; }
        .hero-note { font-size: 13px; color: var(--text-faint); }
        .hero-stats { display: flex; gap: 30px; margin-top: 32px; }
        .stat-num { font-size: 22px; font-weight: 700; }
        .stat-label { font-size: 12px; color: var(--text-muted); }

        /* ── Buttons ── */
        .btn-primary {
            display: inline-flex; align-items: center; justify-content: center; gap: 8px;
            background: var(--accent-grad); color: #fff; border: none; border-radius: 12px;
            font-family: inherit; font-size: 14px; font-weight: 600; padding: 0 22px; height: 46px;
            cursor: pointer; box-shadow: var(--glow); transition: transform .12s, filter .15s, opacity .15s;
        }
        .btn-primary:hover { filter: brightness(1.04); color: #fff; }
        .btn-primary:active { transform: translateY(1px); }
        .btn-primary:disabled { opacity: .65; cursor: default; box-shadow: none; }
        .btn-block { width: 100%; }

        /* ── Tool card ── */
        .tool-card {
            background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg);
            box-shadow: var(--shadow-lg); padding: 24px; scroll-margin-top: 90px;
        }
        .tool-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
        .tool-title { font-size: 15px; font-weight: 600; }
        /* Free-forever chip on the tool card */
        .free-chip {
            display: inline-flex; align-items: center; gap: 6px;
            color: var(--success); background: var(--success-bg);
            border: 1px solid var(--success-border); border-radius: 20px;
            font-weight: 600; white-space: nowrap;
            font-size: 13.5px; padding: 7px 14px; box-shadow: var(--shadow);
        }
        .free-chip svg { width: 14px; height: 14px; flex-shrink: 0; }

        /* ── Fields ── */
        .field { margin-top: 14px; }
        .field > label {
            display: block; font-size: 12px; font-weight: 500; color: var(--text-muted); margin-bottom: 7px;
        }
        .field > label span { color: var(--text-faint); font-weight: 400; }
        .hint { display: block; font-size: 11.5px; color: var(--text-faint); margin-top: 6px; }
        .bl-in, select#provider, #keywords {
            width: 100%; background: var(--bg-elevated); border: 1px solid var(--border);
            border-radius: 12px; color: var(--text); font-family: inherit; font-size: 14px;
            padding: 11px 14px; outline: none; transition: border-color .15s, box-shadow .15s;
        }
        select#provider {
            appearance: none; -webkit-appearance: none; cursor: pointer;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%239AA4B2' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
            background-repeat: no-repeat; background-position: right 12px center; padding-right: 38px;
        }
        .bl-in:focus, select#provider:focus, #keywords:focus {
            border-color: var(--accent); box-shadow: 0 0 0 3px var(--tint);
        }
        #keywords::placeholder, .bl-in::placeholder { color: var(--text-faint); }

        /* ── Drop zone ── */
        .drop-zone {
            border: 1.5px dashed var(--border); border-radius: 12px; padding: 30px 20px;
            display: flex; flex-direction: column; align-items: center; gap: 6px;
            cursor: pointer; transition: border-color .2s, background .2s; text-align: center;
        }
        .drop-zone:hover, .drop-zone.drag-over { border-color: var(--accent); background: var(--tint); }
        .drop-zone svg { width: 30px; height: 30px; color: var(--accent); }
        .drop-label { font-size: 13.5px; color: var(--text-muted); margin-top: 2px; }
        .drop-label strong { color: var(--accent); }
        .drop-sub { font-size: 11.5px; color: var(--text-faint); }

        /* ── File list ── */
        .file-list { list-style: none; margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
        .file-item {
            display: flex; align-items: center; justify-content: space-between;
            background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 9px;
            padding: 8px 12px; font-size: 12.5px; color: var(--text-muted);
            animation: bl-in .18s ease-out;
        }
        .file-item > span { display: flex; align-items: center; gap: 8px; min-width: 0; }
        .file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .remove-btn {
            background: none; border: none; cursor: pointer; color: var(--text-faint);
            font-size: 14px; padding: 2px 5px; line-height: 1;
        }
        .remove-btn:hover { color: var(--error); }

        /* ── Mode chips ── */
        .mode-group { display: flex; gap: 8px; }
        .mode-card {
            flex: 1; position: relative; text-align: center; padding: 10px 6px;
            background: var(--bg-elevated); border: 1.5px solid var(--border); border-radius: 12px;
            cursor: pointer; transition: border-color .15s, box-shadow .15s, background .15s;
        }
        .mode-card:hover { border-color: var(--accent); }
        .mode-card.checked { border-color: var(--accent); box-shadow: 0 0 0 3px var(--tint); background: var(--tint); }
        .mode-card input { position: absolute; opacity: 0; pointer-events: none; }
        .mode-title { font-size: 12px; font-weight: 600; }

        /* ── Inline explainers / tips ── */
        .provider-tip, .mode-explainer {
            display: flex; align-items: flex-start; gap: 9px;
            font-size: 12px; color: var(--text-muted); line-height: 1.55;
            background: var(--tint); border: 1px solid var(--border); border-radius: 10px;
            /* collapsed by default — reveal is animated, not a snap, so the card
               grows smoothly instead of shifting the page in one jump */
            max-height: 0; opacity: 0; margin-top: 0; padding: 0 12px; border-width: 0 1px;
            overflow: hidden;
            transition: max-height .24s ease, opacity .2s ease, margin-top .24s ease,
                        padding .24s ease, border-width .24s ease;
        }
        .provider-tip.visible, .mode-explainer.visible {
            max-height: 150px; opacity: 1; margin-top: 10px; padding: 10px 12px; border-width: 1px;
        }
        @media (prefers-reduced-motion: reduce) {
            .provider-tip, .mode-explainer { transition: none; }
        }
        .provider-tip svg, .mode-explainer svg { width: 15px; height: 15px; color: var(--accent); flex-shrink: 0; margin-top: 1px; }
        .provider-tip strong, .mode-explainer strong { color: var(--text); font-weight: 600; }

        /* ── Enhanced-privacy toggle ── */
        .toggle-row { display: flex; align-items: center; gap: 11px; margin-top: 16px; }
        .toggle-wrap { position: relative; flex-shrink: 0; }
        .toggle-wrap input { position: absolute; opacity: 0; width: 0; height: 0; }
        .toggle-track {
            display: block; width: 38px; height: 22px; border-radius: 20px;
            background: var(--bg-elevated); border: 1px solid var(--border); cursor: pointer;
            transition: background .18s, border-color .18s; position: relative;
        }
        .toggle-thumb {
            position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%;
            background: var(--text-faint); transition: transform .18s, background .18s;
        }
        .toggle-wrap input:checked + .toggle-track { background: var(--accent); border-color: var(--accent); }
        .toggle-wrap input:checked + .toggle-track .toggle-thumb { transform: translateX(16px); background: #fff; }
        .toggle-label-text { font-size: 12.5px; font-weight: 500; color: var(--text); line-height: 1.4; }
        .toggle-label-text small { display: block; font-size: 11.5px; font-weight: 400; color: var(--text-faint); }

        /* ── Spinner ── */
        .spinner {
            display: none; width: 15px; height: 15px; border: 2px solid rgba(255,255,255,.4);
            border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite; margin-left: 8px;
        }
        .spinner.visible { display: inline-block; }

        /* ── Results ── */
        .results-section { margin-top: 14px; }
        .results-list { display: flex; flex-direction: column; gap: 8px; }
        .result-card {
            display: flex; align-items: center; gap: 11px; background: var(--bg-elevated);
            border: 1px solid var(--border); border-radius: 12px; padding: 10px 13px;
            animation: bl-in .2s ease-out;
        }
        .result-icon {
            width: 30px; height: 30px; border-radius: 9px; display: flex; align-items: center;
            justify-content: center; flex-shrink: 0;
        }
        .result-info { flex: 1; min-width: 0; }
        .result-name { font-size: 12.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .result-detail { font-size: 11.5px; color: var(--text-muted); margin-top: 1px; }
        .btn-download {
            display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0;
            font-size: 12px; font-weight: 500; color: var(--text); background: var(--bg-card);
            border: 1px solid var(--border); border-radius: 9px; padding: 7px 12px; transition: border-color .15s;
        }
        .btn-download:hover { border-color: var(--accent); color: var(--accent); }
        .btn-download svg { width: 14px; height: 14px; }

        /* ── Demand-signal banners ── */
        .bank-request-banner, .beta-banner {
            margin-top: 10px; background: var(--warning-bg); border: 1px solid var(--warning-border);
            border-radius: 12px; padding: 12px 14px; font-size: 12.5px; color: var(--text);
        }
        .bank-request-row { display: flex; gap: 8px; margin-top: 9px; }
        .bank-request-row input {
            flex: 1; background: var(--bg-card); border: 1px solid var(--border); border-radius: 9px;
            color: var(--text); font-family: inherit; font-size: 12.5px; padding: 8px 11px; outline: none;
        }
        .bank-request-row input:focus { border-color: var(--accent); }
        .bank-request-row button {
            background: var(--accent); color: #fff; border: none; border-radius: 9px;
            font-family: inherit; font-size: 12.5px; font-weight: 500; padding: 8px 14px; cursor: pointer;
        }
        .bank-request-done { margin-top: 8px; color: var(--success); font-size: 12px; }

        /* ── Feature strip ── */
        /* Full-bleed band: break out of the container's 44px side padding so the
           elevated background spans the whole content column, per the design. */
        .feature-strip {
            background: var(--bg-elevated);
            border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
            padding: 38px 44px; margin: 24px -44px; scroll-margin-top: 80px;
        }
        .feature-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 40px; }
        .feature { display: flex; gap: 16px; align-items: flex-start; }
        .feature-ic {
            width: 48px; height: 48px; border-radius: 16px; background: var(--tint);
            border: 1px solid var(--border); display: flex; align-items: center; justify-content: center;
            flex-shrink: 0; box-shadow: var(--shadow);
        }
        .feature-ic svg { width: 19px; height: 19px; color: var(--accent); }
        .feature h3 { font-size: 14.5px; font-weight: 600; letter-spacing: -.01em; }
        .feature p { font-size: 12.5px; color: var(--text-muted); margin-top: 5px; line-height: 1.65; max-width: 30ch; }

        /* ── Pricing ── */
        .pricing { padding: 48px 0; text-align: center; scroll-margin-top: 70px; }
        .pricing h2 { font-size: 22px; font-weight: 700; letter-spacing: -.02em; }
        .pricing-sub { font-size: 13px; color: var(--text-muted); margin-top: 6px; }
        .pricing-sub a { color: var(--accent-strong); }

        /* ── Custom request ── */
        .custom-section {
            background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg);
            box-shadow: var(--shadow); padding: 28px; margin: 8px 0 44px;
        }
        .custom-section h2 { font-size: 18px; font-weight: 700; }
        .custom-section > p { font-size: 13px; color: var(--text-muted); margin-top: 6px; }

        /* Contact form (replaces the old Google Form iframe) */
        .contact-form { margin-top: 18px; }
        .contact-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
        .contact-grid .field { margin-top: 0; }
        .contact-form .field:not(.contact-grid .field) { margin-top: 14px; }
        .cf-input, .cf-select {
            width: 100%; background: var(--bg-elevated); border: 1px solid var(--border);
            border-radius: 12px; color: var(--text); font-family: inherit; font-size: 14px;
            padding: 11px 14px; outline: none; transition: border-color .15s, box-shadow .15s;
        }
        .cf-textarea { resize: vertical; min-height: 96px; line-height: 1.5; }
        .cf-select {
            appearance: none; -webkit-appearance: none; cursor: pointer;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%239AA4B2' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
            background-repeat: no-repeat; background-position: right 12px center; padding-right: 38px;
        }
        .cf-input:focus, .cf-select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--tint); }
        .cf-input::placeholder { color: var(--text-faint); }
        .contact-form #cf-submit { margin-top: 18px; }
        .contact-error { margin-top: 12px; font-size: 12.5px; color: var(--error); }
        .contact-error a { color: var(--error); }
        .contact-success {
            display: flex; align-items: flex-start; gap: 12px; margin-top: 16px;
            background: var(--success-bg); border: 1px solid var(--success-border);
            border-radius: 12px; padding: 16px 18px; animation: bl-in .25s ease-out;
        }
        /* The [hidden] attribute is display:none by default, but the display:flex
           rule above overrides it — restore hiding until the form is submitted. */
        .contact-success[hidden] { display: none; }
        .contact-success svg { width: 20px; height: 20px; color: var(--success); flex-shrink: 0; margin-top: 1px; }
        .contact-success h3 { font-size: 14px; font-weight: 600; color: var(--text); }
        .contact-success p { font-size: 12.5px; color: var(--text-muted); margin-top: 3px; }
        /* Sample-statement file input */
        .cf-file {
            width: 100%; font-family: inherit; font-size: 13px; color: var(--text-muted);
            background: var(--bg-elevated); border: 1px dashed var(--border);
            border-radius: 12px; padding: 10px 12px; cursor: pointer;
        }
        .cf-file:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--tint); }
        .cf-file::file-selector-button {
            font-family: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer;
            margin-right: 12px; padding: 7px 14px; border: none; border-radius: 8px;
            background: var(--tint); color: var(--accent-strong);
        }
        .cf-sample-field .hint { line-height: 1.5; margin-top: 8px; }
        @media (max-width: 520px) { .contact-grid { grid-template-columns: 1fr; } }

        /* ── Footer ── */
        .site-footer {
            border-top: 1px solid var(--border); padding: 20px 0; text-align: center;
            font-size: 12px; color: var(--text-faint);
        }
        .site-footer a { color: var(--text-muted); }
        .site-footer a:hover { color: var(--accent); }

        /* ── Guide pages (shared chrome) ── */
        .guide { max-width: 720px; margin: 0 auto; padding: 48px 0 8px; }
        .guide h1 { font-size: 32px; font-weight: 700; letter-spacing: -.02em; line-height: 1.15; }
        .guide .guide-lead { font-size: 17px; color: var(--text-muted); line-height: 1.6; margin: 16px 0 8px; }
        .guide h2 { font-size: 20px; font-weight: 600; letter-spacing: -.01em; margin: 32px 0 6px; }
        .guide p { font-size: 15px; color: var(--text); line-height: 1.75; margin: 12px 0; }
        .guide ul { margin: 12px 0 12px 2px; list-style: none; }
        .guide li { position: relative; padding-left: 22px; font-size: 15px; line-height: 1.7; margin: 8px 0; color: var(--text); }
        .guide li::before {
            content: ""; position: absolute; left: 2px; top: 11px; width: 7px; height: 7px;
            border-radius: 50%; background: var(--accent);
        }
        .guide .guide-cta {
            margin: 36px 0 8px; background: var(--bg-elevated); border: 1px solid var(--border);
            border-radius: var(--radius-lg); padding: 24px 26px;
        }
        .guide .guide-cta p { margin: 0 0 14px; font-size: 15px; font-weight: 500; }
        .guide .guide-cta a {
            display: inline-flex; align-items: center; gap: 8px; background: var(--accent-grad); color: #fff;
            border-radius: 12px; font-size: 14px; font-weight: 600; padding: 11px 20px; box-shadow: var(--glow);
        }
        .guide .guide-cta a:hover { filter: brightness(1.04); color: #fff; }

        /* ── Animations ── */
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes bl-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }

        /* ── Responsive ── */
        @media (max-width: 900px) {
            .container { padding: 0 22px; }
            .hero-split { grid-template-columns: 1fr; gap: 32px; padding: 36px 0; }
            .hero-copy h1 { font-size: 34px; }
            .feature-strip { padding: 28px 22px; margin: 16px -22px; }
            .feature-grid { grid-template-columns: 1fr; gap: 22px; }
            .header-right { gap: 14px; }
        }
        @media (max-width: 560px) {
            .hero-stats { gap: 20px; }
            .navlink { display: none; }
        }
    </style>
</head>
<body>
<div class="page-wrap">

    <!-- Header -->
    <header class="site-header">
        <div class="container">
            <div class="header-inner">
                <a class="logo" href="/">
                    <div class="logo-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M5 6.5h14M5 17.5h9"/>
                            <rect x="4" y="9.6" width="13" height="4.8" rx="1.4" fill="#fff" stroke="none"/>
                        </svg>
                    </div>
                    <span class="logo-name">Redactly</span>
                </a>
                <div class="header-right">
                    <a class="navlink" href="/#how">How it works</a>
                    <a class="navlink" href="/#pricing">Pricing</a>
                    {% if usage_count %}<span class="usage-badge">{{ usage_count }} redacted</span>{% endif %}
                    <button class="theme-toggle" id="theme-toggle" title="Toggle theme" onclick="toggleTheme()">
                        <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>
                        </svg>
                        <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="5"/>
                            <line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
                            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
                            <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
                            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    </header>

    <main style="flex:1;">
        <div class="container">
'''

_HOMEPAGE_BODY = '''
            <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "WebApplication",
              "name": "Redactly",
              "applicationCategory": "UtilityApplication",
              "operatingSystem": "Web",
              "offers": {"@type": "Offer", "price": "0", "priceCurrency": "GBP", "description": "Free and open source. No account needed."},
              "description": "Redact bank and credit card statement PDFs with true redaction: keep only chosen transactions visible for rental applications and expense claims. Supports AMEX, Barclaycard, HSBC, Revolut, Wise and other UK banks."
            }
            </script>

            <!-- Split hero -->
            <section class="hero-split">
                <div class="hero-copy">
                    <span class="hero-badge">🔒 Private by design</span>
                    <h1>Share the line.<br>Not the ledger.</h1>
                    <p class="hero-sub">Redactly permanently blacks out every transaction you don't want a landlord or employer to see — the text underneath is destroyed, not just covered. Free, no signup.</p>
                    <div class="hero-cta-row">
                        <a href="#tool" class="btn-primary">Redact my statement</a>
                        <span class="hero-note">Files deleted after download</span>
                    </div>
                    <div class="hero-stats">
                        <div class="stat"><div class="stat-num">{{ usage_count or '—' }}</div><div class="stat-label">statements redacted</div></div>
                        <div class="stat"><div class="stat-num">0</div><div class="stat-label">stored, ever</div></div>
                        <div class="stat"><div class="stat-num">£0</div><div class="stat-label">for your first doc</div></div>
                    </div>
                </div>

                <!-- Live tool card -->
                <div class="tool-card" id="tool">
                    <div class="tool-head">
                        <span class="tool-title">Redact a statement</span>
                        <span id="free-badge" class="free-chip" title="No credits, no account — just free">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/></svg>
                            <span>Free · no signup</span>
                        </span>
                    </div>

                    <!-- File picker -->
                    <div id="drop-zone" class="drop-zone" onclick="document.getElementById('pdf-input').click()">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                            <polyline points="17 8 12 3 7 8"/>
                            <line x1="12" y1="3" x2="12" y2="15"/>
                        </svg>
                        <p class="drop-label">Drop your PDF or <strong>browse</strong></p>
                        <p class="drop-sub">AMEX · Barclaycard · UK banks</p>
                        <input id="pdf-input" type="file" accept=".pdf" multiple style="display:none">
                    </div>
                    <ul id="file-list" class="file-list"></ul>

                    <!-- Provider -->
                    <div class="field">
                        <label for="provider">Card provider</label>
                        <select id="provider">
                            {% for value, display in providers %}
                            <option value="{{ value }}" {% if value == 'auto' %}selected{% endif %}>{{ display }}</option>
                            {% endfor %}
                        </select>
                        <span class="hint">Auto-detect works for most statements</span>
                    </div>
                    <div id="barclaycard-tip" class="provider-tip">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                        </svg>
                        <span><strong>Barclaycard</strong> — precise row-by-row redaction with financial privacy. Enter merchant keywords.</span>
                    </div>

                    <!-- Mode -->
                    <div class="field">
                        <label>What do you need this for?</label>
                        <div class="mode-group" id="mode-group">
                            <label class="mode-card checked" data-mode="custom">
                                <input type="radio" name="mode" value="custom" checked>
                                <span class="mode-title">Expenses</span>
                            </label>
                            <label class="mode-card" data-mode="landlord">
                                <input type="radio" name="mode" value="landlord">
                                <span class="mode-title">Landlord</span>
                            </label>
                            <label class="mode-card" data-mode="custom">
                                <input type="radio" name="mode" value="custom">
                                <span class="mode-title">Custom</span>
                            </label>
                        </div>
                        <div id="landlord-explainer" class="mode-explainer">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                            </svg>
                            <span><strong>Keeps:</strong> money coming in (salary, transfers), your balances, and anything you whitelist (e.g. rent). <strong>Hides:</strong> all other spending. For bank statements, not credit cards.</span>
                        </div>
                    </div>

                    <!-- Keywords -->
                    <div class="field">
                        <label for="keywords">Keep transactions matching <span>(comma-separated)</span></label>
                        <input id="keywords" type="text" placeholder="e.g. rent, Tfl Travel, Hyperoptic">
                    </div>

                    <!-- Enhanced privacy -->
                    <div class="toggle-row">
                        <div class="toggle-wrap">
                            <input type="checkbox" id="enhanced_privacy">
                            <label class="toggle-track" for="enhanced_privacy"><div class="toggle-thumb"></div></label>
                        </div>
                        <label for="enhanced_privacy" class="toggle-label-text" style="cursor:pointer">
                            Enhanced financial privacy
                            <small>Also redact balances, credit limit, and rates — on bank statements, personal details too (card numbers, contact info; landlord mode keeps your name &amp; account number visible as proof)</small>
                        </label>
                    </div>

                    <!-- Submit -->
                    <div class="field">
                        <button id="submit-btn" class="btn-primary btn-block" onclick="processFiles()">
                            <span id="btn-text">Redact my statement</span>
                            <div id="btn-spinner" class="spinner"></div>
                        </button>
                    </div>

                    <!-- Results -->
                    <div id="results-section" class="results-section" style="display:none">
                        <div id="results" class="results-list"></div>
                    </div>
                </div>
            </section>

            <!-- Feature strip / how it works -->
            <section class="feature-strip" id="how">
                <div class="feature-grid">
                    <div class="feature">
                        <div class="feature-ic">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>
                        </div>
                        <div><h3>True redaction</h3><p>Text underneath is destroyed. Extraction finds nothing.</p></div>
                    </div>
                    <div class="feature">
                        <div class="feature-ic">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                        </div>
                        <div><h3>Nothing stored</h3><p>Processed in memory, deleted after download.</p></div>
                    </div>
                    <div class="feature">
                        <div class="feature-ic">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="17" y1="8" x2="22" y2="13"/><line x1="22" y1="8" x2="17" y2="13"/></svg>
                        </div>
                        <div><h3>No account</h3><p>No signup, no email, no tracking.</p></div>
                    </div>
                </div>
            </section>

            <!-- Pricing -->
            <section class="pricing" id="pricing">
                <h2>Free. That's the whole price list.</h2>
                <p class="pricing-sub">No credits, no subscription, no account.</p>
                <p class="pricing-sub">The code is <a href="https://github.com/divyamrastogi/redactly" rel="noopener">open source</a> — use the hosted copy or run it yourself, free either way.</p>
                <p class="pricing-sub">If it saved you time, you can <a href="https://ko-fi.com/javascriptbit" rel="noopener" target="_blank">buy me a Ko-fi</a> — entirely optional.</p>
            </section>

            <!-- Add more providers -->
            <div class="custom-section" id="custom-request">
                <h2>Add more providers</h2>
                <p>Your bank or card isn't supported yet? Send a sample statement and we'll add it — or just tell us what's missing.</p>

                <div class="contact-success" id="contact-success" hidden>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                    <div>
                        <h3>Thanks — we've got it.</h3>
                        <p>We'll get back to you at the email you provided.</p>
                    </div>
                </div>

                <form class="contact-form" id="contact-form" novalidate>
                    <div class="contact-grid">
                        <div class="field">
                            <label for="cf-name">Name</label>
                            <input id="cf-name" class="cf-input" name="name" type="text" required placeholder="Your name" autocomplete="name">
                        </div>
                        <div class="field">
                            <label for="cf-email">Email</label>
                            <input id="cf-email" class="cf-input" name="email" type="email" required placeholder="you@company.com" autocomplete="email">
                        </div>
                    </div>
                    <div class="field">
                        <label for="cf-type">What's it about?</label>
                        <select id="cf-type" class="cf-select" name="project_type" required>
                            <option value="">Select…</option>
                            <option>A bank or card we don't support yet</option>
                            <option>A statement that redacts wrong</option>
                            <option>Something else</option>
                        </select>
                    </div>
                    <div class="field">
                        <label for="cf-details">A few details</label>
                        <textarea id="cf-details" class="cf-input cf-textarea" name="details" required rows="4" placeholder="Tell us a little about what you need."></textarea>
                    </div>
                    <div class="field cf-sample-field">
                        <label for="cf-sample">Sample statement <span>· optional</span></label>
                        <input id="cf-sample" class="cf-file" name="sample" type="file" accept="application/pdf,.pdf">
                        <span class="hint">From a bank we don't support yet? Attach a statement PDF so we can build support for it. Unlike the redaction tool, a sample you share here is emailed to us — <strong>not stored on the site</strong>. Feel free to black out your account number first; we only need the transaction layout.</span>
                    </div>
                    <button type="submit" class="btn-primary btn-block" id="cf-submit">Send message</button>
                    <p class="contact-error" id="contact-error" hidden>Something went wrong. Please try again, or email us at <a href="mailto:divyamrastogi2@gmail.com">divyamrastogi2@gmail.com</a>.</p>
                </form>
            </div>
'''

_SITE_MID = '''
        </div>
    </main>

    <footer class="site-footer">
        <div class="container">
            © 2026 Redactly · True redaction, nothing stored · <a href="/guides">Guides</a> · <a href="https://ko-fi.com/javascriptbit" rel="noopener" target="_blank">Buy me a Ko-fi</a>
        </div>
    </footer>
</div>

<script>
// --- Theme toggle (shared across homepage + guide pages) ---
// Redactly defaults to light; a saved preference or the OS setting still wins.
(function() {
    const saved = localStorage.getItem('theme');
    if (saved === 'dark')  document.documentElement.classList.add('dark');
    if (saved === 'light') document.documentElement.classList.add('light');
    // no saved pref → no class → light by default (system dark honoured via @media)
})();

function toggleTheme() {
    const html = document.documentElement;
    const isDark = html.classList.contains('dark') ||
        (!html.classList.contains('light') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    if (isDark) {
        html.classList.remove('dark');
        html.classList.add('light');
        localStorage.setItem('theme', 'light');
    } else {
        html.classList.remove('light');
        html.classList.add('dark');
        localStorage.setItem('theme', 'dark');
    }
}
</script>
'''

_HOMEPAGE_SCRIPT = '''
<script>
// --- Privacy-safe analytics wrappers (chokepoint + total banding) ---
// track() is defined in the shared site script (_SITE_SCRIPT) so guide pages
// can report too. bandTotal() collapses an exact £ amount into a coarse band
// BEFORE it reaches any tracker.
// Hard privacy rule: never pass statement content, keywords, filenames, or
// exact monetary values to either helper — provider slugs, counts, and bands only.
function bandTotal(pounds) {
    if (typeof pounds !== 'number' || isNaN(pounds) || pounds < 0) return 'unknown';
    if (pounds <= 10)   return '0-10';
    if (pounds <= 50)   return '11-50';
    if (pounds <= 250)  return '51-250';
    if (pounds <= 1000) return '251-1000';
    return '1000+';
}

// Collapse varied exception strings into a few categorical error types, so
// raw error messages (which can echo filenames) never reach analytics.
function categorizeError(message) {
    const m = (message || '').toLowerCase();
    if (m.includes('no transactions') || m.includes('nothing to redact'))
        return 'no_transactions';
    if (m.includes('parse') || m.includes('format'))
        return 'parse_failed';
    if (m.includes('no file') || m.includes('empty filename'))
        return 'no_file';
    if (m.includes('landlord mode'))
        return 'landlord_on_card';
    return 'server_error';
}

// --- Provider UI ---
function getSelectedMode() {
    const checked = document.querySelector('#mode-group input[name="mode"]:checked');
    return checked ? checked.value : 'custom';
}

function refreshKeywordPlaceholder() {
    const kw = document.getElementById('keywords');
    if (getSelectedMode() === 'landlord') {
        kw.placeholder = 'rent, letting agent (optional)';
        return;
    }
    const provider = document.getElementById('provider').value;
    kw.placeholder = (provider === 'barclaycard')
        ? 'e.g. Tfl Travel, Hyperoptic, Your-Saving'
        : 'e.g. Office Supplies, Travel, Client Dinner';
}

function updateProviderUI() {
    const provider = document.getElementById('provider').value;
    const tip = document.getElementById('barclaycard-tip');
    tip.classList.toggle('visible', provider === 'barclaycard');
    refreshKeywordPlaceholder();
}

function applyModeUI() {
    const mode = getSelectedMode();
    document.querySelectorAll('#mode-group .mode-card').forEach(card => {
        card.classList.toggle('checked', card.querySelector('input').checked);
    });
    document.getElementById('landlord-explainer').classList.toggle('visible', mode === 'landlord');
    refreshKeywordPlaceholder();
}

document.getElementById('provider').addEventListener('change', updateProviderUI);
document.querySelectorAll('#mode-group .mode-card input').forEach(input => {
    input.addEventListener('change', () => {
        applyModeUI();
        track('mode_selected', { mode: getSelectedMode() });
    });
});
document.addEventListener('DOMContentLoaded', () => {
    updateProviderUI();
    applyModeUI();
});

// --- File picker ---
const pdfInput = document.getElementById('pdf-input');
const dropZone = document.getElementById('drop-zone');
const fileList = document.getElementById('file-list');

pdfInput.addEventListener('change', () => {
    renderFileList();
    // Count only — never the filename.
    if (pdfInput.files.length) track('file_selected', { file_count: pdfInput.files.length, via: 'picker' });
});

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const dt = new DataTransfer();
    [...(pdfInput.files || [])].forEach(f => dt.items.add(f));
    [...e.dataTransfer.files].filter(f => f.type === 'application/pdf').forEach(f => dt.items.add(f));
    pdfInput.files = dt.files;
    renderFileList();
    if (pdfInput.files.length) track('file_selected', { file_count: pdfInput.files.length, via: 'drop' });
});

function renderFileList() {
    const files = [...pdfInput.files];
    fileList.innerHTML = files.map((f, i) =>
        `<li class="file-item">
            <span>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent);flex-shrink:0">
                    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>
                </svg>
                <span class="file-name">${f.name}</span>
            </span>
            <button class="remove-btn" onclick="removeFile(${i})" title="Remove">✕</button>
        </li>`
    ).join('');
    if (files.some(f => f.name.toLowerCase().includes('barclay'))) {
        document.getElementById('provider').value = 'barclaycard';
        updateProviderUI();
    }
}

function removeFile(idx) {
    const dt = new DataTransfer();
    [...pdfInput.files].forEach((f, i) => { if (i !== idx) dt.items.add(f); });
    pdfInput.files = dt.files;
    renderFileList();
}

// --- Process files ---
async function processFiles() {
    const files    = [...pdfInput.files];
    const keywords = document.getElementById('keywords').value.trim();
    const provider = document.getElementById('provider').value;
    const privacy  = document.getElementById('enhanced_privacy').checked;
    const mode     = getSelectedMode();

    if (!files.length)  { shakeField('drop-zone'); return; }
    // Keywords are required unless landlord mode is selected (income/balances
    // are kept automatically; keywords are optional there).
    if (mode !== 'landlord' && !keywords) { shakeField('keywords'); return; }

    track('redact_clicked', { mode: mode, provider: provider, file_count: files.length, privacy: privacy });

    const btn     = document.getElementById('submit-btn');
    const btnText = document.getElementById('btn-text');
    const spinner = document.getElementById('btn-spinner');
    btn.disabled = true;
    spinner.classList.add('visible');
    btnText.textContent = `Processing 0 / ${files.length}…`;

    const section  = document.getElementById('results-section');
    const results  = document.getElementById('results');
    section.style.display = 'block';

    let done = 0;
    for (const file of files) {
        const card = addPendingCard(file.name);
        results.appendChild(card);
        try {
            const fd = new FormData();
            fd.append('pdf', file);
            fd.append('keywords', keywords);
            fd.append('provider', provider);
            fd.append('mode', mode);
            if (privacy) fd.append('enhanced_privacy', 'on');

            const res  = await fetch('/redact', { method: 'POST', body: fd });
            const data = await res.json();

            if (data.error) {
                track('redact_error', { error_type: categorizeError(data.error) });
                updateCard(card, 'error', file.name, null, data.error);
            } else {
                track('redact_success', {
                    provider: data.provider,
                    detected: data.provider_detected,
                    // Banded total + kept-count only: never the exact amount or
                    // any keyword/merchant/filename content.
                    total_band: bandTotal(data.total),
                    kept_count: data.kept_count
                });
                const detail = data.kept_count >= 0
                    ? `${data.kept_count} transaction${data.kept_count !== 1 ? 's' : ''} · £${data.total.toFixed(2)}`
                    : `£${data.total.toFixed(2)} total`;
                updateCard(card, 'success', data.filename, data.download_url, detail);
                if (data.provider_detected === false) showBankRequestBanner();
                if (data.beta) showBetaBanner();
            }
        } catch (err) {
            track('redact_error', { error_type: categorizeError(err.message) });
            updateCard(card, 'error', file.name, null, err.message);
        }
        done++;
        btnText.textContent = done < files.length ? `Processing ${done} / ${files.length}…` : 'Redact PDFs';
    }

    btn.disabled = false;
    spinner.classList.remove('visible');
    btnText.textContent = 'Redact PDFs';
}

function showBankRequestBanner() {
    const section = document.getElementById('results-section');
    if (!section || document.getElementById('bank-request-banner')) return;

    const banner = document.createElement('div');
    banner.className = 'bank-request-banner';
    banner.id = 'bank-request-banner';
    banner.innerHTML = `
        <p>We couldn't confidently detect your bank. Which bank is this statement from?</p>
        <div class="bank-request-row">
            <input id="bank-request-input" type="text" placeholder="e.g. Monzo, HSBC, NatWest" maxlength="60">
            <button id="bank-request-btn" type="button">Submit</button>
        </div>
        <p class="bank-request-done" id="bank-request-done" style="display:none">Thanks — that helps us add support.</p>`;
    section.appendChild(banner);

    const input = document.getElementById('bank-request-input');
    const btn   = document.getElementById('bank-request-btn');
    const done  = document.getElementById('bank-request-done');

    async function submit() {
        const bank = input.value.trim();
        if (!bank) { input.focus(); return; }
        btn.disabled = true;
        btn.textContent = '…';
        try {
            await fetch('/bank-request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bank })
            });
            // Count only — the typed bank name goes to our own server log, not GA.
            track('bank_request');
            done.style.display = 'block';
            btn.textContent = 'Submitted';
            input.disabled = true;
        } catch (e) {
            btn.disabled = false;
            btn.textContent = 'Submit';
        }
    }

    btn.addEventListener('click', submit);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
}

function showBetaBanner() {
    const section = document.getElementById('results-section');
    if (!section || document.getElementById('beta-banner')) return;
    track('bank_beta');
    const banner = document.createElement('div');
    banner.className = 'beta-banner';
    banner.id = 'beta-banner';
    banner.innerHTML = '<p>Bank statement support is in beta — please check every page of the output before sharing it.</p>';
    section.appendChild(banner);
}

function addPendingCard(filename) {
    const card = document.createElement('div');
    card.className = 'result-card';
    card.innerHTML = `
        <div class="result-icon" style="background:var(--bg-elevated)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                 style="animation:spin 0.7s linear infinite">
                <line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/>
                <line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/>
                <line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/>
                <line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/>
            </svg>
        </div>
        <div class="result-info">
            <p class="result-name">${filename}</p>
            <p class="result-detail">Processing…</p>
        </div>`;
    return card;
}

function updateCard(card, status, filename, url, detail) {
    card.className = `result-card ${status}`;
    if (status === 'success') {
        card.innerHTML = `
            <div class="result-icon" style="background:rgba(16,185,129,0.12)">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--success)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"/>
                </svg>
            </div>
            <div class="result-info">
                <p class="result-name">${filename}</p>
                <p class="result-detail">${detail}</p>
            </div>
            <a href="${url}" download class="btn-download" onclick="track('download_clicked', { provider: document.getElementById('provider').value })">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                Download
            </a>`;
    } else {
        card.innerHTML = `
            <div class="result-icon" style="background:rgba(248,113,113,0.1)">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--error)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </div>
            <div class="result-info">
                <p class="result-name">${filename}</p>
                <p class="result-detail">${detail}</p>
            </div>`;
    }
}

function shakeField(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.animation = 'none';
    el.style.outline = '2px solid var(--error)';
    setTimeout(() => { el.style.outline = ''; }, 1200);
}

// --- "Add more providers" contact form ---
// Two paths:
//  • No sample attached → post straight to Supabase REST with the PUBLISHABLE
//    key. Safe in the browser: the table's RLS allows insert only. A Postgres
//    trigger emails us via Brevo. No content hits the Flask app.
//  • Sample PDF attached → post multipart to /contact-sample. Flask detects the
//    provider (so the email says which bank it is) and emails the PDF to us
//    (never stored).
(function () {
    var CONTACT_SUPABASE_URL = 'https://pnjsyklmibspekxgslos.supabase.co';
    var CONTACT_SUPABASE_KEY = 'sb_publishable_K0QR3oL-s0n6PqsPIrJh2g_vCuBDTEE';
    var CONTACT_TABLE = 'redactly_contact_submissions';

    var form = document.getElementById('contact-form');
    if (!form) return;
    var success = document.getElementById('contact-success');
    var errorMsg = document.getElementById('contact-error');
    var fileInput = document.getElementById('cf-sample');
    var button = document.getElementById('cf-submit');

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (!form.reportValidity()) return;

        errorMsg.hidden = true;
        button.disabled = true;
        var original = button.textContent;
        button.textContent = 'Sending…';

        var fail = function () {
            errorMsg.hidden = false;
            button.disabled = false;
            button.textContent = original;
        };

        // data === null for the Supabase path; an object for /contact-sample.
        var done = function (data) {
            form.hidden = true;
            if (success) success.hidden = false;
            if (typeof track === 'function') {
                track('contact_submit', { sample: !!(data && data.reason && data.reason !== 'no_file') });
            }
        };

        var hasFile = fileInput && fileInput.files && fileInput.files.length > 0;

        if (hasFile) {
            // Multipart → Flask. FormData(form) carries name/email/project_type/
            // details/sample by their name attributes.
            fetch('/contact-sample', { method: 'POST', body: new FormData(form) })
                .then(function (res) {
                    return res.json().catch(function () { return {}; })
                        .then(function (json) { res.ok ? done(json) : fail(); });
                })
                .catch(fail);
            return;
        }

        // No sample → Supabase REST (publishable key: apikey header only for
        // sb_publishable_ keys; legacy JWT keys also need Authorization: Bearer).
        var payload = {
            name: form.elements.name.value.trim(),
            email: form.elements.email.value.trim(),
            project_type: form.elements.project_type.value,
            details: form.elements.details.value.trim(),
        };
        var headers = {
            'Content-Type': 'application/json',
            'apikey': CONTACT_SUPABASE_KEY,
            'Prefer': 'return=minimal',
        };
        if (CONTACT_SUPABASE_KEY.indexOf('eyJ') === 0) {
            headers['Authorization'] = 'Bearer ' + CONTACT_SUPABASE_KEY;
        }
        fetch(CONTACT_SUPABASE_URL + '/rest/v1/' + CONTACT_TABLE, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(payload),
        }).then(function (res) {
            res.ok ? done(null) : fail();
        }).catch(fail);
    });
})();
</script>
'''

_SITE_SCRIPT = '''
<script>
// --- Shared analytics chokepoint ---
// track() funnels every analytics event through a single function and fans out
// to GA4 (gtag) and Umami (cookieless, blocker-resilient). Each tracker is
// best-effort: if one is blocked or never loaded, the call no-ops.
// Hard privacy rule: never pass statement content, keywords, filenames, or
// exact monetary values — provider slugs, counts, and bands only.
function track(event, params) {
    params = params || {};
    if (typeof gtag === 'function') {
        try { gtag('event', event, params); } catch (e) { /* best-effort */ }
    }
    if (window.umami && typeof window.umami.track === 'function') {
        try { window.umami.track(event, params); } catch (e) { /* best-effort */ }
    }
}

// Growth signals shared by every page: outbound clicks (GitHub stars, Ko-fi)
// and guide → homepage CTA clicks (which guides convert readers into users?).
document.addEventListener('click', function (e) {
    const a = e.target.closest('a[href]');
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (href.includes('github.com/')) {
        track('outbound_click', { target: 'github' });
    } else if (href.includes('ko-fi.com/')) {
        track('outbound_click', { target: 'kofi' });
    } else if (location.pathname.startsWith('/guides/') && (href === '/' || href.startsWith('/#'))) {
        track('guide_cta_click', { slug: location.pathname.split('/')[2] || '' });
    }
});
</script>
'''

_SITE_END = '''
</body>
</html>
'''

# Homepage template = shared shell + homepage-only body and scripts.
HTML_TEMPLATE = _SITE_OPEN + _HOMEPAGE_BODY + _SITE_MID + _SITE_SCRIPT + _HOMEPAGE_SCRIPT + _SITE_END

class LandlordCardError(ValueError):
    """Landlord mode was requested for a credit-card statement.

    Landlord mode (keep income + balances, hide other spending) only makes sense
    for bank statements; card statements have no income/balance semantics worth
    keeping, so the /redact endpoint turns this into a friendly 400.
    """


def process_single_file(file, keywords, provider, enhanced_privacy, mode='custom', keep_credits=False):
    """Process one uploaded PDF.

    Returns (redacted_path, total, kept_count, provider_detected, provider_name, beta)
    where provider_detected is True if the provider was identified by content or
    chosen manually, provider_name is the slug reported back to the frontend, and
    beta is True when the generic bank parser (beta) handled this file.

    ``mode`` is 'custom' (default, current behaviour) or 'landlord' (keep credits
    + optional keyword whitelist). ``keep_credits`` is forwarded to the bank /
    barclaycard parsers. Raises ``LandlordCardError`` if landlord mode targets a
    credit-card statement (amex_uk / barclaycard) — the caller returns a 400.

    ``enhanced_privacy`` on the generic-bank path enables the Presidio PII pass
    (pii_mode='enforce', degrading to 'off' with a warning when presidio isn't
    installed) plus balance/summary redaction (redact_balances=True).
    """
    import uuid, fitz as _fitz

    tmp_in = f"tmp_in_{uuid.uuid4().hex}.pdf"
    file.save(tmp_in)
    try:
        base_name = os.path.splitext(file.filename)[0]
        tmp_out   = f"tmp_out_{uuid.uuid4().hex}.pdf"

        filename_lower = file.filename.lower()
        provider_detected = True
        report_provider = provider
        beta = False

        # Auto-detect provider from PDF content when none was chosen
        if provider == 'auto':
            detected = None
            try:
                _doc  = _fitz.open(tmp_in)
                _text = _doc[0].get_text() if len(_doc) > 0 else ''
                _doc.close()
                detected = detect_provider(_text)
                # Preserve legacy text signal: Barclaycard Avios statements are
                # routed to the barclaycard path even without the brand word.
                if detected is None and 'mastercard avios' in _text.lower():
                    detected = 'barclaycard'
            except Exception:
                detected = None

            if detected is not None:
                provider = detected
                report_provider = detected
                provider_detected = True
            else:
                # Unknown provider: record a demand signal (NO document content),
                # then fall back to the existing AMEX processing path.
                log_unrecognized_upload()
                provider = 'amex_uk'        # explicit AMEX fallback for processing
                report_provider = 'unknown'  # honest report to the frontend
                provider_detected = False

        # HSBC, Revolut, and Wise are named providers sharing the generic bank parser.
        is_generic_bank = provider in ('generic_bank_uk', 'hsbc', 'revolut', 'wise')
        is_barclaycard = (
            provider == 'barclaycard' or
            'barclaycard' in filename_lower or
            'barclay' in filename_lower
        )

        # Landlord mode is for bank statements only. Once the provider is resolved
        # (detected from content or chosen manually), reject credit-card statements
        # with a friendly 400 — but only when we actually identified the provider
        # (an undetected statement falls back to the AMEX path and must not be
        # rejected as a guess).
        if (mode == 'landlord' and provider_detected
                and provider in ('amex_uk', 'barclaycard')):
            raise LandlordCardError(
                'Landlord mode is for bank statements. For card statements, '
                'use Expense mode with keywords.'
            )

        if is_generic_bank:
            # Generic UK bank statement parser (BETA) — layout-driven.
            # Enhanced privacy adds the Presidio PII pass (name, account
            # numbers, card numbers — landlord mode auto-preserves ownership
            # details) and the balance/summary redaction. If presidio isn't
            # installed the PII half degrades gracefully to balances-only.
            pii_mode = 'off'
            if enhanced_privacy:
                from pii_layer import _HAS_PRESIDIO
                if _HAS_PRESIDIO:
                    pii_mode = 'enforce'
                else:
                    app.logger.warning(
                        'enhanced_privacy requested but presidio-analyzer is '
                        'not installed — applying balance redaction only')
            redacted_path, total, kept = redact_bank_generic(
                tmp_in, tmp_out, keywords, keep_credits=keep_credits,
                pii_mode=pii_mode, redact_balances=enhanced_privacy)
            kept_count = len(kept)
            beta = True
        elif is_barclaycard:
            redacted_path, total, kept = redact_barclaycard(
                tmp_in, tmp_out, keywords, keep_credits=keep_credits)
            kept_count   = len(kept)
        elif enhanced_privacy:
            redacted_path, total = redact_amex_with_privacy(tmp_in, keywords, tmp_out, redact_financial=True)
            kept_count = -1  # AMEX doesn't return count
        else:
            redacted_path, total = redact_pdf_generic(tmp_in, keywords, tmp_out, provider)
            kept_count = -1

        # Rename with total in filename
        final_name = f"redacted_{base_name}_£{total:.2f}.pdf"
        if os.path.exists(redacted_path):
            os.rename(redacted_path, final_name)
            redacted_path = final_name

        return redacted_path, total, kept_count, provider_detected, report_provider, beta
    finally:
        if os.path.exists(tmp_in):
            os.remove(tmp_in)


@app.route('/', methods=['GET'])
def index():
    usage_count = update_usage_counter()
    providers   = get_all_providers()
    return render_template_string(HTML_TEMPLATE,
        title='Redactly — Share Bank & Card Statements Without Oversharing',
        meta_description='Blackout every transaction on your AMEX or Barclaycard statement except the ones you choose. For rental applications and expense claims. True redaction — text is destroyed, not hidden. Files deleted after download.',
        canonical=_base_url() + '/',
        usage_count=usage_count,
        providers=providers)


@app.route('/guides/<slug>')
def guide_page(slug):
    """Render a single SEO guide page in the shared site shell.

    Guide bodies are plain HTML (no Jinja syntax) sourced from guides.GUIDES.
    Unknown slugs 404.
    """
    from guides import GUIDES
    guide = GUIDES.get(slug)
    if guide is None:
        abort(404)
    import json as _json
    jsonld = '<script type="application/ld+json">' + _json.dumps({
        '@context': 'https://schema.org',
        '@type': 'Article',
        'headline': guide['title'],
        'description': guide['meta_description'],
        'url': _base_url() + '/guides/' + slug,
        'publisher': {'@type': 'Organization', 'name': 'Redactly',
                      'url': _base_url()},
    }) + '</script>'
    template = _SITE_OPEN + jsonld + guide['html_body'] + _SITE_MID + _SITE_SCRIPT + _SITE_END
    return render_template_string(template,
        title=guide['title'],
        meta_description=guide['meta_description'],
        canonical=_base_url() + '/guides/' + slug,
        og_type='article')


@app.route('/guides')
def guides_index():
    """Index of all guides — target of the footer link, aids internal linking."""
    from guides import GUIDES
    items = ''.join(
        f'<li style="margin-bottom:14px"><a href="/guides/{slug}">{g["title"]}</a>'
        f'<br><span style="color:var(--text-muted)">{g["meta_description"]}</span></li>'
        for slug, g in GUIDES.items()
    )
    body = (
        '<article class="guide"><h1>Guides</h1>'
        '<p>Practical, honest guides to sharing financial documents without oversharing.</p>'
        f'<ul style="list-style:none;padding:0;margin-top:24px">{items}</ul></article>'
    )
    template = _SITE_OPEN + body + _SITE_MID + _SITE_SCRIPT + _SITE_END
    return render_template_string(template,
        title='Guides — Redactly',
        meta_description='Guides to redacting bank and card statements for rental applications and expense claims.',
        canonical=_base_url() + '/guides')


def _base_url():
    """Canonical site root for absolute URLs (sitemap/robots). Env-configurable."""
    return os.environ.get('BASE_URL', 'https://redact.javascriptbit.com').rstrip('/')


@app.route('/sitemap.xml')
def sitemap():
    """XML sitemap: homepage + every guide. Absolute URLs from BASE_URL."""
    from guides import GUIDES
    from xml.sax.saxutils import escape
    urls = ([_base_url() + '/', _base_url() + '/guides']
            + [_base_url() + '/guides/' + slug for slug in GUIDES])
    lastmod = _DEPLOY_DATE
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in urls:
        lines.append('  <url>')
        lines.append('    <loc>' + escape(url) + '</loc>')
        lines.append('    <lastmod>' + lastmod + '</lastmod>')
        lines.append('  </url>')
    lines.append('</urlset>')
    return Response('\n'.join(lines) + '\n', mimetype='application/xml')


@app.route('/robots.txt')
def robots():
    """Allow all crawlers — explicitly including AI assistants — + sitemap."""
    lines = ['User-agent: *', 'Allow: /', '']
    # Named allows for AI crawlers: redundant with * but unambiguous, and makes
    # the welcome explicit if a blanket rule is ever tightened later.
    for bot in ('GPTBot', 'ClaudeBot', 'anthropic-ai', 'PerplexityBot',
                'Google-Extended', 'CCBot'):
        lines += [f'User-agent: {bot}', 'Allow: /', '']
    lines += ['Sitemap: ' + _base_url() + '/sitemap.xml', '']
    return Response('\n'.join(lines), mimetype='text/plain')


@app.route('/llms.txt')
def llms_txt():
    """Machine-readable site summary for AI assistants (llms.txt convention)."""
    from guides import GUIDES
    base = _base_url()
    guide_lines = '\n'.join(
        f"- [{g['title']}]({base}/guides/{slug}): {g['meta_description']}"
        for slug, g in GUIDES.items()
    )
    body = f"""# Redactly

> Web tool that redacts bank and credit-card statement PDFs using true
> redaction (text destroyed, not covered). Users keep only the transactions
> they choose visible — for UK rental applications and expense claims — while
> names, statement periods, and balances stay intact.

Key facts:
- Pricing: free, and open source under the MIT licence. No subscription, no
  account, no credits.
- Supported statements: American Express, Barclaycard, HSBC, Revolut, Wise,
  plus a generic parser for other UK bank layouts (beta).
- Landlord mode keeps income, balances, and whitelisted rows (e.g. rent) and
  hides all other spending. Expense mode keeps only keyword-matched rows.
- True redaction: removed text is destroyed and cannot be copied or extracted,
  unlike drawn black boxes.
- Files are processed in memory on the server and deleted immediately after
  download. No account or signup.
- Not suitable for mortgage underwriting or UK visa applications — those
  generally require unredacted statements.

## Guides
{guide_lines}

## App
- [Redact a statement]({base}/): upload a PDF, pick a mode, download the result.
"""
    return Response(body, mimetype='text/plain')


@app.route('/redact', methods=['POST'])
def redact_endpoint():
    """Process a single PDF and return JSON with download URL."""
    update_usage_counter()

    if 'pdf' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file     = request.files['pdf']
    keywords = [k.strip() for k in request.form.get('keywords', '').split(',') if k.strip()]
    provider = request.form.get('provider', 'auto')
    enhanced = request.form.get('enhanced_privacy') == 'on'
    mode     = request.form.get('mode', 'custom')
    is_landlord = (mode == 'landlord')

    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400
    # Keywords are required in custom/expense mode. In landlord mode they are
    # optional (the user may keep only income + balances).
    if not is_landlord and not keywords:
        return jsonify({'error': 'No keywords provided'}), 400

    try:
        redacted_path, total, kept_count, provider_detected, detected_provider, beta = process_single_file(
            file, keywords, provider, enhanced,
            mode=mode, keep_credits=is_landlord
        )
        display_name = os.path.basename(redacted_path)
        return jsonify({
            'filename':         display_name,
            'download_url':     f'/download/{redacted_path}',
            'total':            total,
            'kept_count':       kept_count,
            'provider_detected': provider_detected,
            'provider':         detected_provider,
            'beta':             bool(beta),
        })
    except LandlordCardError as e:
        # Friendly 400: landlord mode doesn't apply to credit-card statements.
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Redact error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/download/<path:filename>')
def download_file(filename):
    @after_this_request
    def cleanup(response):
        try:
            os.remove(filename)
            logger.info(f"Deleted file: {filename}")
        except Exception as e:
            logger.error(f"Error deleting file {filename}: {str(e)}")
        return response

    return send_file(filename, as_attachment=True)


@app.route('/bank-request', methods=['POST'])
def bank_request():
    """Capture a user-typed bank name when auto-detection fails.

    Privacy: store only a sanitized bank name + ISO date — never statement content.
    """
    data = request.get_json(silent=True) or {}
    raw = str(data.get('bank') or '').strip()
    # Sanitize: alphanumeric + spaces only, capped at 60 chars
    clean = re.sub(r'[^A-Za-z0-9 ]', '', raw)[:60].strip()
    if not clean:
        return jsonify({'error': 'empty bank name'}), 400
    try:
        with open('bank_requests.txt', 'a') as f:
            f.write(f"{_iso_now()},{clean}\n")
    except Exception as e:
        logger.error(f"Error writing bank_requests.txt: {str(e)}")
        return jsonify({'error': 'storage failure'}), 500
    return jsonify({'ok': True})


# Contact form + optional sample statement. The text-only form posts straight
# to Supabase from the browser; only submissions that ATTACH a sample come here,
# because provider detection runs server-side (so we can tell which bank the
# statement is from in the email we receive). The sample PDF is emailed to us
# and never stored — same "nothing stored" promise as the redaction tool itself.
_CONTACT_NOTIFY_FN_URL = 'https://pnjsyklmibspekxgslos.supabase.co/functions/v1/redactly-contact-notify'
_CONTACT_ANON_KEY = 'sb_publishable_K0QR3oL-s0n6PqsPIrJh2g_vCuBDTEE'  # publishable, public by design
_SAMPLE_MAX_BYTES = 8 * 1024 * 1024           # 8 MB — statements are small
_SAMPLE_MIN_TEXT_CHARS = 300                  # blocks blank / junk PDFs
_SUPPORTED_PROVIDERS = {'amex_uk', 'barclaycard'}  # already have full configs


def _notify_contact(record, attachment=None):
    """POST the submission (with optional base64 attachment) to the contact
    Edge Function, which emails it via Brevo. Returns True on success."""
    payload = {'record': record}
    if attachment:
        payload['attachment'] = attachment
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        _CONTACT_NOTIFY_FN_URL, data=data, method='POST',
        headers={'Content-Type': 'application/json', 'apikey': _CONTACT_ANON_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200 <= r.status < 300
    except Exception as e:
        logger.error(f"contact notify failed: {e}")
        return False


@app.route('/contact-sample', methods=['POST'])
def contact_sample():
    """Contact submission that includes an optional sample statement.

    Runs provider detection on the attached PDF so the email we receive says
    which bank it is from. The PDF is emailed to us and never stored.
    """
    name    = (request.form.get('name') or '').strip()
    email   = (request.form.get('email') or '').strip()
    ptype   = (request.form.get('project_type') or '').strip()
    details = (request.form.get('details') or '').strip()
    if not name or not email or not details:
        return jsonify({'error': 'Please fill in your name, email, and a message.'}), 400

    attachment = None
    reason = 'no_file'
    detected = None

    file = request.files.get('sample')
    if file and file.filename:
        raw = file.read()
        if len(raw) > _SAMPLE_MAX_BYTES:
            return jsonify({'error': 'That file is larger than 8 MB. Please attach a single statement PDF.'}), 413

        # Must be a readable PDF — extract text for detection + a junk filter.
        text = ''
        try:
            import fitz
            doc = fitz.open(stream=raw, filetype='pdf')
            text = ''.join(page.get_text() for page in doc)
            doc.close()
        except Exception:
            return jsonify({'error': "That doesn't look like a readable PDF. Please attach a statement PDF."}), 400

        detected = detect_provider(text)
        attachment = {
            'name': secure_filename(file.filename) or 'sample.pdf',
            'content': base64.b64encode(raw).decode('ascii'),
        }

        # Reason is reported to the client only so the UI can say something
        # useful; no rewards, no accounts, nothing to claim.
        if detected in _SUPPORTED_PROVIDERS:
            reason = 'already_supported'
        elif len(text.strip()) < _SAMPLE_MIN_TEXT_CHARS:
            reason = 'unreadable'
        else:
            reason = 'statement'

    # Give us context in the email without storing anything.
    if attachment:
        details = (f"{details}\n\n— — —\n[sample attached: {attachment['name']} · "
                   f"detected: {detected or 'unknown'}]")
    record = {'name': name, 'email': email, 'project_type': ptype, 'details': details}

    if not _notify_contact(record, attachment):
        return jsonify({'error': "Something went wrong sending your message. Please email us at divyamrastogi2@gmail.com."}), 502

    return jsonify({'ok': True, 'reason': reason})


@app.route('/stats', methods=['GET'])
def stats():
    """Token-gated demand-signal stats. 404 unless STATS_TOKEN env matches."""
    import hmac
    expected = os.environ.get('STATS_TOKEN')
    if not expected or not hmac.compare_digest(request.args.get('token', ''), expected):
        abort(404)

    # Usage counter value
    try:
        with open('usage_counter.txt') as f:
            usage_count = int((f.read() or '0').strip())
    except Exception:
        usage_count = 0

    # Unrecognized-upload line count
    try:
        with open('unrecognized_uploads.txt') as f:
            unrecognized_uploads = sum(1 for line in f if line.strip())
    except Exception:
        unrecognized_uploads = 0

    # Tail-20 of bank requests
    try:
        with open('bank_requests.txt') as f:
            bank_requests = [line.strip() for line in f if line.strip()][-20:]
    except Exception:
        bank_requests = []

    return jsonify({
        'usage_count': usage_count,
        'unrecognized_uploads': unrecognized_uploads,
        'bank_requests_tail': bank_requests,
    })


if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get('PORT', 5001)))


@app.route('/debug-redact', methods=['POST'])
def debug_redact():
    """Debug endpoint — returns transaction list without redacting."""
    import uuid, fitz as _fitz
    from redact_barclaycard import group_transactions, COLUMNS
    
    file = request.files.get('pdf')
    if not file:
        return jsonify({'error': 'no file'}), 400
    
    tmp = f"tmp_dbg_{uuid.uuid4().hex}.pdf"
    file.save(tmp)
    try:
        doc = _fitz.open(tmp)
        result = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            txs, start_y, end_y = group_transactions(page)
            for tx in txs:
                result.append({
                    'page': page_num + 1,
                    'date': tx['date'],
                    'merchant': tx['merchant'],
                    'amount': tx['amount'],
                    'col': tx.get('col', 0),
                })
        doc.close()
        return jsonify({'count': len(result), 'transactions': result})
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@app.route('/debug-spans', methods=['POST'])
def debug_spans():
    """Return raw span x/y data for first 20 transaction-area spans."""
    import uuid, fitz as _fitz
    file = request.files.get('pdf')
    if not file: return jsonify({'error': 'no file'}), 400
    tmp = f"tmp_sp_{uuid.uuid4().hex}.pdf"
    file.save(tmp)
    try:
        doc = _fitz.open(tmp)
        page = doc[1]
        spans = []
        for block in page.get_text("dict")["blocks"]:
            if "lines" not in block: continue
            for line in block["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    if t:
                        spans.append({"x": round(span["bbox"][0],1), "y": round(span["bbox"][1],1), "t": t[:40]})
        doc.close()
        # Return spans sorted by y, x — just the transaction area
        spans.sort(key=lambda s: (s["y"], s["x"]))
        return jsonify({"total_spans": len(spans), "spans": spans[:80]})
    finally:
        if os.path.exists(tmp): os.remove(tmp)
