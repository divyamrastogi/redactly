from flask import Flask, request, send_file, after_this_request, render_template_string, jsonify, abort, redirect, Response
import os
import re
import logging
from datetime import datetime, timezone
from redact_transactions import redact_transactions
from redact_generic import redact_pdf_generic
from redact_financial_details import redact_barclaycard_with_privacy, redact_amex_with_privacy
from redact_barclaycard import redact_barclaycard
from redact_bank_generic import redact_bank_generic
from provider_config import get_all_providers, detect_provider
import payments

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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;550;600;700&display=swap" rel="stylesheet">
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-SY9PXXMVD8"></script>
    <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', 'G-SY9PXXMVD8');
    </script>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        /* ── Dark theme (default) ── */
        :root {
            --bg:             #0a0a0b;
            --bg-card:        #111113;
            --bg-elevated:    #18181b;
            --border:         rgba(255,255,255,0.07);
            --border-focus:   rgba(139,92,246,0.6);
            --text:           #f4f4f5;
            --text-muted:     #71717a;
            --text-faint:     #3f3f46;
            --accent:         #8b5cf6;
            --accent-glow:    rgba(139,92,246,0.15);
            --accent-hover:   #7c3aed;
            --success:        #10b981;
            --success-bg:     rgba(16,185,129,0.08);
            --success-border: rgba(16,185,129,0.2);
            --error:          #f87171;
            --error-bg:       rgba(248,113,113,0.08);
            --error-border:   rgba(248,113,113,0.2);
            --warning:        #f59e0b;
            --warning-border: rgba(245,158,11,0.28);
            --radius:         10px;
            --radius-lg:      14px;
            --shadow:         0 1px 3px rgba(0,0,0,0.4), 0 4px 16px rgba(0,0,0,0.3);
        }

        /* ── Light theme ── */
        .light {
            --bg:             #fafafa;
            --bg-card:        #ffffff;
            --bg-elevated:    #f4f4f5;
            --border:         rgba(0,0,0,0.08);
            --border-focus:   rgba(109,40,217,0.5);
            --text:           #18181b;
            --text-muted:     #52525b;
            --text-faint:     #a1a1aa;
            --accent:         #7c3aed;
            --accent-glow:    rgba(124,58,237,0.10);
            --accent-hover:   #6d28d9;
            --success:        #059669;
            --success-bg:     rgba(5,150,105,0.06);
            --success-border: rgba(5,150,105,0.2);
            --error:          #dc2626;
            --error-bg:       rgba(220,38,38,0.06);
            --error-border:   rgba(220,38,38,0.2);
            --warning:        #d97706;
            --warning-border: rgba(217,119,6,0.26);
            --shadow:         0 1px 3px rgba(0,0,0,0.07), 0 4px 16px rgba(0,0,0,0.05);
        }

        /* ── System default (prefers dark → dark vars already on :root) ── */
        @media (prefers-color-scheme: light) {
            :root:not(.dark) {
                --bg:             #fafafa;
                --bg-card:        #ffffff;
                --bg-elevated:    #f4f4f5;
                --border:         rgba(0,0,0,0.08);
                --border-focus:   rgba(109,40,217,0.5);
                --text:           #18181b;
                --text-muted:     #52525b;
                --text-faint:     #a1a1aa;
                --accent:         #7c3aed;
                --accent-glow:    rgba(124,58,237,0.10);
                --accent-hover:   #6d28d9;
                --success:        #059669;
                --success-bg:     rgba(5,150,105,0.06);
                --success-border: rgba(5,150,105,0.2);
                --error:          #dc2626;
                --error-bg:       rgba(220,38,38,0.06);
                --error-border:   rgba(220,38,38,0.2);
                --warning:        #d97706;
                --warning-border: rgba(217,119,6,0.26);
                --shadow:         0 1px 3px rgba(0,0,0,0.07), 0 4px 16px rgba(0,0,0,0.05);
            }
        }

        html, body {
            min-height: 100vh;
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            font-size: 14px;
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }

        /* ── Layout ── */
        .page-wrap {
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        .container {
            width: 100%;
            max-width: 880px;
            margin: 0 auto;
            padding: 0 24px;
        }

        /* ── Header ── */
        .site-header {
            border-bottom: 1px solid var(--border);
            padding: 20px 0;
        }

        .header-inner {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 10px;
            text-decoration: none;
        }

        .logo-icon {
            width: 32px;
            height: 32px;
            background: var(--accent);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }

        .logo-icon svg {
            width: 18px;
            height: 18px;
            color: white;
        }

        .logo-name {
            font-size: 16px;
            font-weight: 600;
            color: var(--text);
            letter-spacing: -0.02em;
        }

        .usage-badge {
            font-size: 12px;
            color: var(--text-muted);
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            padding: 4px 10px;
            border-radius: 20px;
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        /* ── Theme toggle button ── */
        .theme-toggle {
            width: 34px;
            height: 34px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            border-radius: 8px;
            cursor: pointer;
            color: var(--text-muted);
            transition: color 0.15s, background 0.15s, border-color 0.15s;
            flex-shrink: 0;
        }

        .theme-toggle:hover {
            color: var(--text);
            border-color: var(--accent);
        }

        .theme-toggle svg {
            width: 16px;
            height: 16px;
        }

        /* show/hide sun vs moon */
        .icon-sun  { display: none; }
        .icon-moon { display: block; }
        .light .icon-sun  { display: block; }
        .light .icon-moon { display: none; }
        @media (prefers-color-scheme: light) {
            :root:not(.dark) .icon-sun  { display: block; }
            :root:not(.dark) .icon-moon { display: none; }
        }

        /* ── Hero ── */
        .hero {
            padding: 56px 0 40px;
            text-align: center;
        }

        .hero-eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--accent);
            background: var(--accent-glow);
            border: 1px solid rgba(139,92,246,0.25);
            padding: 4px 12px;
            border-radius: 20px;
            margin-bottom: 20px;
        }

        .hero h1 {
            font-size: clamp(28px, 5vw, 42px);
            font-weight: 700;
            letter-spacing: -0.03em;
            line-height: 1.15;
            color: var(--text);
            margin-bottom: 14px;
        }

        .hero p {
            font-size: 16px;
            color: var(--text-muted);
            max-width: 480px;
            margin: 0 auto;
            line-height: 1.65;
        }

        /* ── Cards ── */
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow);
        }

        .main-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }

        @media (max-width: 680px) {
            .main-grid { grid-template-columns: 1fr; }
        }

        /* ── How it works card ── */
        .how-card {
            padding: 28px;
        }

        .how-card h2 {
            font-size: 15px;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 12px;
            letter-spacing: -0.01em;
        }

        .how-card p {
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 16px;
            line-height: 1.65;
        }

        .how-list {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 20px;
        }

        .how-list li {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            font-size: 13px;
            color: var(--text-muted);
        }

        .how-list li::before {
            content: '';
            width: 5px;
            height: 5px;
            border-radius: 50%;
            background: var(--accent);
            margin-top: 7px;
            flex-shrink: 0;
        }

        .example-keywords {
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 12px 16px;
        }

        .example-keywords p {
            font-size: 12px;
            color: var(--text-faint);
            margin-bottom: 6px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            font-weight: 500;
        }

        .example-keywords code {
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 12px;
            color: var(--accent);
        }

        /* ── Form card ── */
        .form-card {
            padding: 28px;
        }

        .form-card h2 {
            font-size: 15px;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 22px;
            letter-spacing: -0.01em;
        }

        .form-stack {
            display: flex;
            flex-direction: column;
            gap: 18px;
        }

        .field label {
            display: block;
            font-size: 12px;
            font-weight: 500;
            color: var(--text-muted);
            margin-bottom: 7px;
            letter-spacing: 0.01em;
        }

        .field label span {
            color: var(--text-faint);
            font-weight: 400;
        }

        .field select,
        .field input[type="text"] {
            width: 100%;
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            color: var(--text);
            font-family: inherit;
            font-size: 13.5px;
            padding: 9px 13px;
            outline: none;
            transition: border-color 0.15s, box-shadow 0.15s;
            appearance: none;
            -webkit-appearance: none;
        }

        .field select {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2371717a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 12px center;
            padding-right: 36px;
            cursor: pointer;
        }

        .field select:focus,
        .field input[type="text"]:focus {
            border-color: var(--border-focus);
            box-shadow: 0 0 0 3px var(--accent-glow);
        }

        .field input::placeholder {
            color: var(--text-faint);
        }

        .hint {
            display: block;
            font-size: 11.5px;
            color: var(--text-faint);
            margin-top: 5px;
        }

        /* ── Barclaycard tip ── */
        .provider-tip {
            display: none;
            align-items: flex-start;
            gap: 10px;
            background: rgba(139,92,246,0.06);
            border: 1px solid rgba(139,92,246,0.2);
            border-radius: var(--radius);
            padding: 11px 14px;
            font-size: 12.5px;
            color: #c4b5fd;
        }

        .provider-tip.visible { display: flex; }

        .provider-tip svg {
            width: 15px;
            height: 15px;
            flex-shrink: 0;
            margin-top: 1px;
            color: var(--accent);
        }

        /* ── Mode selector (radio cards) ── */
        .mode-group {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .mode-card {
            position: relative;
            display: block;
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 11px 14px;
            cursor: pointer;
            transition: border-color 0.15s, box-shadow 0.15s;
        }

        .mode-card input[type="radio"] {
            position: absolute;
            opacity: 0;
            width: 0;
            height: 0;
        }

        .mode-card:hover {
            border-color: var(--border-focus);
        }

        .mode-card.checked {
            border-color: var(--accent);
            box-shadow: 0 0 0 3px var(--accent-glow);
        }

        .mode-title {
            display: block;
            font-size: 13px;
            font-weight: 600;
            color: var(--text);
            letter-spacing: -0.01em;
        }

        .mode-card.checked .mode-title {
            color: var(--accent);
        }

        .mode-desc {
            display: block;
            font-size: 11.5px;
            font-weight: 400;
            color: var(--text-faint);
            margin-top: 2px;
            line-height: 1.45;
        }

        .mode-explainer {
            display: none;
            align-items: flex-start;
            gap: 10px;
            background: rgba(139,92,246,0.06);
            border: 1px solid rgba(139,92,246,0.2);
            border-radius: var(--radius);
            padding: 11px 14px;
            font-size: 12.5px;
            color: var(--text-muted);
            line-height: 1.5;
        }

        .mode-explainer.visible { display: flex; }

        .mode-explainer svg {
            width: 15px;
            height: 15px;
            flex-shrink: 0;
            margin-top: 1px;
            color: var(--accent);
        }

        /* ── Drop zone ── */
        .drop-zone {
            position: relative;
            border: 1.5px dashed var(--border);
            border-radius: var(--radius);
            padding: 28px 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 8px;
            cursor: pointer;
            transition: border-color 0.2s, background 0.2s;
            background: transparent;
        }

        .drop-zone:hover,
        .drop-zone.drag-over {
            border-color: var(--accent);
            background: var(--accent-glow);
        }

        .drop-zone svg {
            width: 28px;
            height: 28px;
            color: var(--text-faint);
            transition: color 0.2s;
        }

        .drop-zone:hover svg,
        .drop-zone.drag-over svg {
            color: var(--accent);
        }

        .drop-label {
            font-size: 13px;
            color: var(--text-muted);
        }

        .drop-label strong {
            color: var(--accent);
            font-weight: 500;
        }

        .drop-sub {
            font-size: 11.5px;
            color: var(--text-faint);
        }

        /* ── File list ── */
        .file-list {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 4px;
            margin-top: 8px;
        }

        .file-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            border-radius: 7px;
            padding: 7px 11px;
            font-size: 12.5px;
            color: var(--text-muted);
            animation: slide-in 0.18s ease-out;
        }

        .file-item span {
            display: flex;
            align-items: center;
            gap: 7px;
            min-width: 0;
        }

        .file-item .file-name {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .remove-btn {
            background: none;
            border: none;
            cursor: pointer;
            color: var(--text-faint);
            font-size: 14px;
            line-height: 1;
            padding: 2px 5px;
            border-radius: 4px;
            transition: color 0.15s, background 0.15s;
            flex-shrink: 0;
        }

        .remove-btn:hover {
            color: var(--error);
            background: var(--error-bg);
        }

        /* ── Toggle / Checkbox ── */
        .toggle-row {
            display: flex;
            align-items: flex-start;
            gap: 12px;
        }

        .toggle-wrap {
            position: relative;
            flex-shrink: 0;
            margin-top: 1px;
        }

        .toggle-wrap input[type="checkbox"] {
            position: absolute;
            opacity: 0;
            width: 0;
            height: 0;
        }

        .toggle-track {
            display: flex;
            align-items: center;
            width: 36px;
            height: 20px;
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            border-radius: 20px;
            cursor: pointer;
            transition: background 0.2s, border-color 0.2s;
        }

        .toggle-wrap input:checked ~ .toggle-track {
            background: var(--accent);
            border-color: var(--accent);
        }

        .toggle-thumb {
            width: 14px;
            height: 14px;
            background: white;
            border-radius: 50%;
            margin-left: 2px;
            transition: transform 0.2s;
            box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        }

        .toggle-wrap input:checked ~ .toggle-track .toggle-thumb {
            transform: translateX(16px);
        }

        .toggle-label-text {
            font-size: 13px;
            font-weight: 500;
            color: var(--text);
        }

        .toggle-label-text small {
            display: block;
            font-size: 11.5px;
            font-weight: 400;
            color: var(--text-faint);
            margin-top: 2px;
        }

        /* ── Submit button ── */
        .btn-primary {
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 10px 20px;
            background: var(--accent);
            color: white;
            font-family: inherit;
            font-size: 13.5px;
            font-weight: 600;
            letter-spacing: -0.01em;
            border: none;
            border-radius: var(--radius);
            cursor: pointer;
            transition: background 0.15s, transform 0.1s, box-shadow 0.15s;
            box-shadow: 0 1px 2px rgba(0,0,0,0.3), 0 0 0 0 var(--accent-glow);
        }

        .btn-primary:hover {
            background: var(--accent-hover);
            box-shadow: 0 2px 8px rgba(139,92,246,0.4);
        }

        .btn-primary:active {
            transform: scale(0.985);
        }

        .btn-primary:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }

        .spinner {
            display: none;
            width: 15px;
            height: 15px;
            border: 2px solid rgba(255,255,255,0.3);
            border-top-color: white;
            border-radius: 50%;
            animation: spin 0.7s linear infinite;
        }

        .spinner.visible { display: block; }

        /* ── Results ── */
        .results-section {
            margin-bottom: 24px;
        }

        .results-heading {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-muted);
            margin-bottom: 10px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

        .results-list {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        /* ── Bank-request banner (auto-detection missed) ── */
        .bank-request-banner {
            margin-top: 12px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-left: 3px solid var(--accent);
            border-radius: var(--radius);
            padding: 14px 16px;
            animation: slide-in 0.2s ease-out;
        }
        .bank-request-banner p {
            font-size: 13px;
            color: var(--text);
            margin-bottom: 10px;
            letter-spacing: -0.01em;
        }
        .bank-request-row {
            display: flex;
            gap: 8px;
        }
        .bank-request-row input {
            flex: 1;
            min-width: 0;
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            color: var(--text);
            font-family: inherit;
            font-size: 13.5px;
            padding: 9px 13px;
            outline: none;
            transition: border-color 0.15s, box-shadow 0.15s;
        }
        .bank-request-row input:focus {
            border-color: var(--border-focus);
            box-shadow: 0 0 0 3px var(--accent-glow);
        }
        .bank-request-row input::placeholder { color: var(--text-faint); }
        .bank-request-row button {
            padding: 9px 16px;
            background: var(--accent);
            color: white;
            font-family: inherit;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: -0.01em;
            border: none;
            border-radius: var(--radius);
            cursor: pointer;
            white-space: nowrap;
            transition: background 0.15s, transform 0.1s;
        }
        .bank-request-row button:hover { background: var(--accent-hover); }
        .bank-request-row button:active { transform: scale(0.98); }
        .bank-request-row button:disabled { opacity: 0.5; cursor: not-allowed; }
        .bank-request-done {
            font-size: 12px;
            color: var(--success);
            margin-top: 8px;
            margin-bottom: 0;
        }

        /* ── Beta banner (generic bank-statement path) ── */
        .beta-banner {
            margin-top: 12px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-left: 3px solid var(--warning);
            border-radius: var(--radius);
            padding: 12px 16px;
            animation: slide-in 0.2s ease-out;
        }
        .beta-banner p {
            font-size: 13px;
            color: var(--text);
            margin: 0;
            letter-spacing: -0.01em;
            line-height: 1.45;
        }

        .result-card {
            display: flex;
            align-items: center;
            gap: 14px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 13px 16px;
            animation: slide-in 0.2s ease-out;
        }

        .result-card.success {
            border-color: var(--success-border);
            background: var(--success-bg);
        }

        .result-card.error {
            border-color: var(--error-border);
            background: var(--error-bg);
        }

        .result-icon {
            flex-shrink: 0;
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .result-icon svg {
            width: 16px;
            height: 16px;
        }

        .result-card .pending .result-icon {
            background: var(--bg-elevated);
        }

        .result-info {
            flex: 1;
            min-width: 0;
        }

        .result-name {
            font-size: 13px;
            font-weight: 500;
            color: var(--text);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .result-detail {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 2px;
        }

        .result-card.error .result-detail {
            color: var(--error);
        }

        .btn-download {
            flex-shrink: 0;
            display: flex;
            align-items: center;
            gap: 6px;
            background: rgba(16,185,129,0.12);
            border: 1px solid rgba(16,185,129,0.3);
            color: var(--success);
            font-family: inherit;
            font-size: 12px;
            font-weight: 500;
            padding: 6px 12px;
            border-radius: 7px;
            text-decoration: none;
            transition: background 0.15s;
        }

        .btn-download:hover {
            background: rgba(16,185,129,0.2);
        }

        .btn-download svg {
            width: 13px;
            height: 13px;
        }

        /* ── Pay toast (post-purchase redirect) ── */
        .pay-toast {
            position: fixed;
            left: 50%;
            bottom: 28px;
            transform: translateX(-50%);
            display: flex;
            align-items: center;
            gap: 10px;
            background: var(--bg-card);
            border: 1px solid var(--success-border);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow);
            padding: 12px 18px;
            font-size: 13px;
            color: var(--text);
            z-index: 50;
            animation: slide-in 0.25s ease-out;
        }
        .pay-toast.error { border-color: var(--error-border); }
        .pay-toast svg { width: 16px; height: 16px; color: var(--success); flex-shrink: 0; }
        .pay-toast.error svg { color: var(--error); }

        /* ── Custom request ── */
        .custom-section {
            margin-bottom: 48px;
        }

        .custom-section h2 {
            font-size: 15px;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 6px;
            letter-spacing: -0.01em;
        }

        .custom-section p {
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 20px;
        }

        .iframe-wrap {
            border-radius: var(--radius-lg);
            overflow: hidden;
            border: 1px solid var(--border);
        }

        .iframe-wrap iframe {
            width: 100%;
            height: 560px;
            display: block;
            border: none;
        }

        /* ── Footer ── */
        .site-footer {
            margin-top: auto;
            border-top: 1px solid var(--border);
            padding: 20px 0;
            text-align: center;
            font-size: 12px;
            color: var(--text-faint);
        }

        .site-footer a {
            color: var(--text-muted);
            text-decoration: none;
            transition: color 0.15s;
        }

        .site-footer a:hover {
            color: var(--text);
        }

        /* ── Trust cards (homepage) ── */
        .trust-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 16px;
            margin-bottom: 28px;
        }

        @media (max-width: 680px) {
            .trust-grid { grid-template-columns: 1fr; }
        }

        .trust-card {
            padding: 22px;
        }

        .trust-card .trust-icon {
            width: 30px;
            height: 30px;
            border-radius: 8px;
            background: var(--accent-glow);
            border: 1px solid rgba(139,92,246,0.2);
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 12px;
        }

        .trust-card .trust-icon svg {
            width: 16px;
            height: 16px;
            color: var(--accent);
        }

        .trust-card h3 {
            font-size: 13.5px;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 8px;
            letter-spacing: -0.01em;
        }

        .trust-card p {
            font-size: 12.5px;
            color: var(--text-muted);
            line-height: 1.65;
        }

        /* ── Guide pages ── */
        .guide {
            max-width: 720px;
            margin: 40px auto 56px;
        }

        .guide h1 {
            font-size: clamp(24px, 4vw, 32px);
            font-weight: 700;
            letter-spacing: -0.02em;
            line-height: 1.2;
            color: var(--text);
            margin-bottom: 12px;
        }

        .guide .guide-lead {
            font-size: 15px;
            color: var(--text-muted);
            margin-bottom: 28px;
            line-height: 1.6;
        }

        .guide h2 {
            font-size: 18px;
            font-weight: 600;
            color: var(--text);
            margin: 28px 0 10px;
            letter-spacing: -0.01em;
        }

        .guide p {
            font-size: 14.5px;
            color: var(--text-muted);
            line-height: 1.7;
            margin-bottom: 14px;
        }

        .guide ul {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin: 0 0 16px;
        }

        .guide li {
            font-size: 14px;
            color: var(--text-muted);
            padding-left: 18px;
            position: relative;
            line-height: 1.6;
        }

        .guide li::before {
            content: '';
            position: absolute;
            left: 0;
            top: 9px;
            width: 5px;
            height: 5px;
            border-radius: 50%;
            background: var(--accent);
        }

        .guide .guide-cta {
            margin-top: 32px;
            padding: 20px 24px;
            background: var(--accent-glow);
            border: 1px solid rgba(139,92,246,0.25);
            border-radius: var(--radius-lg);
            text-align: center;
        }

        .guide .guide-cta p {
            color: var(--text);
            font-size: 14px;
            margin-bottom: 14px;
        }

        .guide .guide-cta a {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 20px;
            background: var(--accent);
            color: white;
            font-family: inherit;
            font-size: 13.5px;
            font-weight: 600;
            letter-spacing: -0.01em;
            border: none;
            border-radius: var(--radius);
            text-decoration: none;
            transition: background 0.15s;
        }

        .guide .guide-cta a:hover {
            background: var(--accent-hover);
        }

        /* ── Animations ── */
        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        @keyframes slide-in {
            from { opacity: 0; transform: translateY(6px); }
            to   { opacity: 1; transform: translateY(0); }
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
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="3" width="18" height="18" rx="3"/>
                            <line x1="9" y1="9" x2="15" y2="9"/>
                            <line x1="9" y1="13" x2="12" y2="13"/>
                        </svg>
                    </div>
                    <span class="logo-name">Redact</span>
                </a>
                <div class="header-right">
                    {% if usage_count %}
                    <span class="usage-badge">{{ usage_count }} statements processed</span>
                    {% endif %}
                    <button class="theme-toggle" id="theme-toggle" title="Toggle theme" onclick="toggleTheme()">
                        <!-- Moon (shown in dark mode) -->
                        <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>
                        </svg>
                        <!-- Sun (shown in light mode) -->
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
              "name": "Redact Statements",
              "applicationCategory": "UtilityApplication",
              "operatingSystem": "Web",
              "offers": {"@type": "Offer", "price": "0", "priceCurrency": "GBP"},
              "description": "Redact bank and credit card statement PDFs with true redaction: keep only chosen transactions visible for rental applications and expense claims. Supports AMEX, Barclaycard, HSBC, Revolut, Wise and other UK banks."
            }
            </script>
            <!-- Hero -->
            <section class="hero">
                <div class="hero-eyebrow">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10"/></svg>
                    True redaction · Files deleted after download
                </div>
                <h1>Share your statement.<br>Not your whole life.</h1>
                <p>Landlords and employers only need to see certain transactions. Upload your statement, choose what stays visible, and every other transaction is permanently blacked out — the text underneath is destroyed, not just covered.</p>
            </section>

            <!-- Main two-col -->
            <div class="main-grid">

                <!-- How it works -->
                <div class="card how-card">
                    <h2>How it works</h2>
                    <p>Upload one or more statement PDFs, choose what should stay visible, and download redacted files as they finish.</p>
                    <ul class="how-list">
                        <li>Multi-file upload — process several months at once</li>
                        <li>Auto-detects AMEX, Barclaycard, and UK bank statement formats</li>
                        <li>Each file is ready to download as soon as it's done</li>
                        <li>Filename shows the whitelisted total for easy reference</li>
                    </ul>
                    <div class="example-keywords">
                        <p>Example keywords</p>
                        <code>Tfl Travel, Hyperoptic, Your-Saving</code>
                    </div>
                </div>

                <!-- Form -->
                <div class="card form-card">
                    <h2>Redact statements</h2>
                    <div class="form-stack">

                        <!-- Provider -->
                        <div class="field">
                            <label>Card provider</label>
                            <select id="provider">
                                {% for value, display in providers %}
                                <option value="{{ value }}" {% if value == 'auto' %}selected{% endif %}>{{ display }}</option>
                                {% endfor %}
                            </select>
                            <span class="hint">Auto-detect works for most statements</span>
                        </div>

                        <!-- Barclaycard tip -->
                        <div id="barclaycard-tip" class="provider-tip">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                            </svg>
                            <span><strong>Barclaycard</strong> — precise row-by-row redaction with financial privacy. Enter merchant keywords.</span>
                        </div>

                        <!-- File picker -->
                        <div class="field">
                            <label>PDF statements <span>(one or more)</span></label>
                            <div id="drop-zone" class="drop-zone" onclick="document.getElementById('pdf-input').click()">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                                    <polyline points="17 8 12 3 7 8"/>
                                    <line x1="12" y1="3" x2="12" y2="15"/>
                                </svg>
                                <p class="drop-label">Drop PDFs here or <strong>browse</strong></p>
                                <p class="drop-sub">PDF files only</p>
                                <input id="pdf-input" type="file" accept=".pdf" multiple style="display:none">
                            </div>
                            <ul id="file-list" class="file-list"></ul>
                        </div>

                        <!-- Mode selector -->
                        <div class="field">
                            <label>What do you need this for?</label>
                            <div class="mode-group" id="mode-group">
                                <label class="mode-card checked" data-mode="custom">
                                    <input type="radio" name="mode" value="custom" checked>
                                    <span class="mode-title">Expense claim</span>
                                    <span class="mode-desc">Keep only transactions matching your keywords.</span>
                                </label>
                                <label class="mode-card" data-mode="landlord">
                                    <input type="radio" name="mode" value="landlord">
                                    <span class="mode-title">Landlord / rental</span>
                                    <span class="mode-desc">Keep income, balances, and rent. Hide other spending.</span>
                                </label>
                                <label class="mode-card" data-mode="custom">
                                    <input type="radio" name="mode" value="custom">
                                    <span class="mode-title">Custom</span>
                                    <span class="mode-desc">Keep only the transactions you specify.</span>
                                </label>
                            </div>
                            <div id="landlord-explainer" class="mode-explainer">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                                </svg>
                                <span><strong>Keeps:</strong> money coming in (salary, transfers), your balances, and any transactions you whitelist (e.g. rent). <strong>Hides:</strong> all other spending.</span>
                            </div>
                            <span class="hint">Landlord mode is for bank statements — not credit cards</span>
                        </div>

                        <!-- Keywords -->
                        <div class="field">
                            <label>Keywords to keep <span>(comma-separated)</span></label>
                            <input id="keywords" type="text" placeholder="e.g. Tfl Travel, Hyperoptic, Your-Saving">
                        </div>

                        <!-- Enhanced privacy toggle -->
                        <div class="toggle-row">
                            <div class="toggle-wrap">
                                <input type="checkbox" id="enhanced_privacy">
                                <label class="toggle-track" for="enhanced_privacy">
                                    <div class="toggle-thumb"></div>
                                </label>
                            </div>
                            <label for="enhanced_privacy" class="toggle-label-text" style="cursor:pointer">
                                Enhanced financial privacy
                                <small>Also redact balances, credit limit, and rates</small>
                            </label>
                        </div>

                        <!-- Submit -->
                        <button id="submit-btn" class="btn-primary" onclick="processFiles()">
                            <span id="btn-text">Redact PDFs</span>
                            <div id="btn-spinner" class="spinner"></div>
                        </button>

                    </div>
                </div>
            </div>

            <!-- Trust -->
            <section class="trust-grid">
                <div class="card trust-card">
                    <div class="trust-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/>
                        </svg>
                    </div>
                    <h3>True redaction</h3>
                    <p>We use PDF redaction annotations that destroy the text underneath. Copy-paste and text extraction find nothing — unlike drawing black boxes, which can be reversed.</p>
                </div>
                <div class="card trust-card">
                    <div class="trust-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>
                        </svg>
                    </div>
                    <h3>Nothing is kept</h3>
                    <p>Your statement is processed in memory on our server and the file is deleted immediately after you download it. We never read, store, or log your transactions.</p>
                </div>
                <div class="card trust-card">
                    <div class="trust-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M20 6L9 17l-5-5"/>
                        </svg>
                    </div>
                    <h3>No account needed</h3>
                    <p>No signup, no email, no tracking of who you are. Upload, redact, download, done.</p>
                </div>
            </section>

            <!-- Results -->
            <div id="results-section" class="results-section" style="display:none">
                <p class="results-heading">Redacted files</p>
                <div id="results" class="results-list"></div>
            </div>

            <!-- Custom request -->
            <div class="card custom-section" id="custom-request" style="padding:28px">
                <h2>Need a custom solution?</h2>
                <p>Get in touch if you need a tailored redaction workflow for your business or use case.</p>
                <div class="iframe-wrap">
                    <iframe src="https://docs.google.com/forms/d/e/1FAIpQLSd2PkHw7ATLfQYwL0CwdkKOnLynPU6mRweu5Zs5PCkKBeVB1g/viewform?usp=sf_link">Loading…</iframe>
                </div>
            </div>
'''

_SITE_MID = '''
        </div>
    </main>

    <footer class="site-footer">
        <div class="container">
            &copy; 2024 Redact &middot; <a href="/guides">Guides</a> &middot; All rights reserved.
        </div>
    </footer>
</div>

<script>
// --- Theme toggle (shared across homepage + guide pages) ---
// Reads saved preference; falls back to system default (no class = system)
(function() {
    const saved = localStorage.getItem('theme');
    if (saved === 'dark')  document.documentElement.classList.add('dark');
    if (saved === 'light') document.documentElement.classList.add('light');
    // no saved pref → no class → @media prefers-color-scheme kicks in
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
// track() funnels every analytics event through a single function and no-ops if
// gtag is blocked (ad blocker) or never loaded, so a missing tracker never
// throws. bandTotal() collapses an exact £ amount into a coarse band BEFORE it
// reaches Google Analytics.
// Hard privacy rule: never pass statement content, keywords, filenames, or
// exact monetary values to either helper — provider slugs, counts, and bands only.
function track(event, params) {
    if (typeof gtag !== 'function') return;
    try {
        gtag('event', event, params || {});
    } catch (e) {
        /* gtag unavailable — analytics is best-effort, never fatal */
    }
}

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
    if (m.includes('no_credits'))
        return 'no_credits';
    return 'server_error';
}

// --- Post-purchase toast (redirect target /?pay=success|already|unpaid|error) ---
(function () {
    const pay = new URLSearchParams(location.search).get('pay');
    if (!pay) return;
    const msg = {
        success: 'Payment received — your credits have been added.',
        already: 'This payment was already used.',
        unpaid:  'Payment is not yet complete.',
        error:   'We could not verify your payment. Please try again.'
    }[pay];
    if (!msg) return;
    const isError = pay !== 'success';
    const toast = document.createElement('div');
    toast.className = 'pay-toast' + (isError ? ' error' : '');
    toast.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            ${isError
                ? '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'
                : '<polyline points="20 6 9 17 4 12"/>'}
        </svg>
        <span>${msg}</span>`;
    document.body.appendChild(toast);
    history.replaceState(null, '', '/');   // drop ?pay= so a refresh won't re-toast
    setTimeout(() => toast.remove(), 6000);
})();

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

pdfInput.addEventListener('change', renderFileList);

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
});

function renderFileList() {
    const files = [...pdfInput.files];
    fileList.innerHTML = files.map((f, i) =>
        `<li class="file-item">
            <span>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:#8b5cf6;flex-shrink:0">
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

            // No credits left (payments enabled) — show a paywall card and stop,
            // since the remaining files would 402 too.
            if (data.error === 'no_credits') {
                track('paywall_shown');
                showPaywall(card, file.name);
                break;
            } else if (data.error) {
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

function showPaywall(card, filename) {
    // Shown when /redact returns 402 no_credits (payments enabled).
    card.className = 'result-card';
    card.innerHTML = `
        <div class="result-icon" style="background:rgba(245,158,11,0.12)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--warning)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/>
            </svg>
        </div>
        <div class="result-info">
            <p class="result-name">${filename}</p>
            <p class="result-detail">You've used your free document.</p>
            <p class="result-detail" style="margin-top:6px">£2.99 for one document · £9.99 for five. No account needed.</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0">
            <a href="/buy?pack=single" class="btn-download" style="background:var(--accent);color:#fff;border-color:var(--accent);justify-content:center;padding:8px 14px">Buy £2.99</a>
            <a href="/buy?pack=pack5" style="font-size:12px;color:var(--text-muted);text-decoration:underline;text-align:center">5 for £9.99</a>
        </div>`;
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
            <a href="${url}" download class="btn-download">
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
</script>
'''

_SITE_END = '''
</body>
</html>
'''

# Homepage template = shared shell + homepage-only body and scripts.
HTML_TEMPLATE = _SITE_OPEN + _HOMEPAGE_BODY + _SITE_MID + _HOMEPAGE_SCRIPT + _SITE_END

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
            redacted_path, total, kept = redact_bank_generic(
                tmp_in, tmp_out, keywords, keep_credits=keep_credits)
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
        title='Redact Statements — Share Bank & Card Statements Without Oversharing',
        meta_description='Blackout every transaction on your AMEX or Barclaycard statement except the ones you choose. For rental applications and expense claims. True redaction — text is destroyed, not hidden. Files deleted after download.',
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
        'publisher': {'@type': 'Organization', 'name': 'Redact Statements',
                      'url': _base_url()},
    }) + '</script>'
    template = _SITE_OPEN + jsonld + guide['html_body'] + _SITE_MID + _SITE_END
    return render_template_string(template,
        title=guide['title'],
        meta_description=guide['meta_description'])


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
    template = _SITE_OPEN + body + _SITE_MID + _SITE_END
    return render_template_string(template,
        title='Guides — Redact Statements',
        meta_description='Guides to redacting bank and card statements for rental applications and expense claims.')


def _base_url():
    """Canonical site root for absolute URLs (sitemap/robots). Env-configurable."""
    return os.environ.get('BASE_URL', 'https://pdf-redact.onrender.com').rstrip('/')


@app.route('/sitemap.xml')
def sitemap():
    """XML sitemap: homepage + every guide. Absolute URLs from BASE_URL."""
    from guides import GUIDES
    from xml.sax.saxutils import escape
    urls = [_base_url() + '/'] + [_base_url() + '/guides/' + slug for slug in GUIDES]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in urls:
        lines.append('  <url>')
        lines.append('    <loc>' + escape(url) + '</loc>')
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
    body = f"""# Redact Statements

> Free web tool that redacts bank and credit-card statement PDFs using true
> redaction (text destroyed, not covered). Users keep only the transactions
> they choose visible — for UK rental applications and expense claims — while
> names, statement periods, and balances stay intact.

Key facts:
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

    # --- Payments (Phase 4): gate on credits BEFORE doing any work. ----------
    # When payments are OFF this whole block is skipped — no cookie read, no
    # decrement — so the free, unlimited behaviour is unchanged.
    enabled = payments.payments_enabled()
    credits = None
    if enabled:
        credits = payments.get_credits(request)
        if credits is None:
            # First document free: a brand-new visitor starts with one credit.
            credits = payments.FREE_CREDITS
        if credits <= 0:
            return jsonify({'error': 'no_credits', 'buy_url': '/buy'}), 402

    try:
        redacted_path, total, kept_count, provider_detected, detected_provider, beta = process_single_file(
            file, keywords, provider, enhanced,
            mode=mode, keep_credits=is_landlord
        )
        display_name = os.path.basename(redacted_path)
        resp = jsonify({
            'filename':         display_name,
            'download_url':     f'/download/{redacted_path}',
            'total':            total,
            'kept_count':       kept_count,
            'provider_detected': provider_detected,
            'provider':         detected_provider,
            'beta':             bool(beta),
        })
        # Spend one credit only after a successful redaction (never on failure).
        if enabled:
            payments.set_credits_cookie(resp, max(credits - 1, 0))
        return resp
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


# ─────────────────────────────────────────────────────────────────────────────
# Payments (Phase 4, Task 4.1). Every route below 404s unless payments are
# enabled, so the disabled (today's production) state exposes none of them.
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/buy', methods=['GET'])
def buy():
    """Create a Stripe Checkout Session for ?pack=single|pack5 and redirect to it."""
    if not payments.payments_enabled():
        abort(404)
    pack = request.args.get('pack', 'single')
    if pack not in payments.PACK_CREDITS:
        return jsonify({'error': 'unknown pack'}), 400
    try:
        session = payments.create_checkout_session(pack)
    except Exception as e:
        logger.error(f"Stripe create session error: {e}", exc_info=True)
        return jsonify({'error': 'checkout failed'}), 502
    return redirect(session.url)


@app.route('/paid', methods=['GET'])
def paid():
    """Stripe success redirect: verify paid + not-yet-consumed, then grant credits.

    Query: ``session_id``. Credit granting happens here; the webhook is only the
    audit trail. Idempotent — a session grants credits at most once.
    """
    if not payments.payments_enabled():
        abort(404)
    session_id = request.args.get('session_id')
    if not session_id:
        abort(400)
    try:
        session = payments.retrieve_session(session_id)
    except Exception as e:
        logger.error(f"Stripe retrieve session error: {e}", exc_info=True)
        return redirect('/?pay=error')

    payment_status = (session.get('payment_status')
                      if isinstance(session, dict)
                      else getattr(session, 'payment_status', None))
    if payment_status != 'paid':
        return redirect('/?pay=unpaid')

    # claim_session atomically checks-and-marks; False means already granted.
    if not payments.claim_session(session_id):
        return redirect('/?pay=already')

    grant = payments.credits_for_session(session)
    current = payments.get_credits(request)
    if current is None:
        current = 0
    resp = redirect('/?pay=success')
    payments.set_credits_cookie(resp, current + grant)
    return resp


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Stripe webhook: verify signature, log completed sessions (audit only).

    Cookie granting happens on ``/paid``; this endpoint only records that a
    checkout completed — session id + amount, never statement content.
    """
    if not payments.payments_enabled():
        abort(404)
    signature = request.headers.get('Stripe-Signature', '')
    try:
        event = payments.verify_webhook(request.get_data(), signature)
    except Exception as e:
        logger.warning(f"Stripe webhook signature verification failed: {e}")
        return jsonify({'error': 'invalid signature'}), 400

    # event may be a plain dict (tests) or a stripe StripeObject (production);
    # both support the field names used below.
    event_type = event.get('type') if isinstance(event, dict) else getattr(event, 'type', None)
    if event_type != 'checkout.session.completed':
        return jsonify({'received': True})

    data = event.get('data') if isinstance(event, dict) else getattr(event, 'data', None)
    obj = (data.get('object') if isinstance(data, dict)
           else getattr(data, 'object', None)) or {}
    sid = obj.get('id', '') if isinstance(obj, dict) else getattr(obj, 'id', '')
    amount = (obj.get('amount_total') if isinstance(obj, dict)
              else getattr(obj, 'amount_total', None))
    try:
        with open('payments_log.txt', 'a') as f:
            amt = '' if amount is None else f',{amount}'
            f.write(f"{_iso_now()},{sid}{amt}\n")
    except Exception as e:
        logger.error(f"Error writing payments_log.txt: {e}")
    return jsonify({'received': True})


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
