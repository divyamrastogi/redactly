# Monetization Phases Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the pdf-redact tool into a monetizable product: instrument demand, reposition for the two validated use cases (rental applications, expense claims), widen provider coverage, add scenario presets, and scaffold per-document payments.

**Architecture:** Single Flask app (`app.py`) with inline HTML template, provider configs in `provider_config.py`, redaction engines in `redact_generic.py` / `redact_barclaycard.py` / `redact_financial_details.py`. All phases build on this — no framework changes, no database. Counters and demand signals stay file-based. Payments are feature-flagged via env vars and no-op when unconfigured.

**Tech Stack:** Flask, PyMuPDF (fitz) 1.27.2, Tailwind (CDN, inline template), Google Analytics (gtag, already wired), Stripe Checkout (Phase 4, flagged), pytest for new tests.

**Research grounding (2026-07-14):** No competitor does transaction-whitelist redaction (all are PII-pattern or manual box tools). Market pricing: ~$5–7.50/doc, $12 day pass, $29/mo. Landlords and employers ACCEPT redacted statements; mortgage underwriting and UKVI REJECT them — so no visa/mortgage positioning anywhere. Biggest known copy bug: the app claims "Runs locally" and Visa/Mastercard support; both are false and must be fixed (trust is the moat).

**Testing convention:** New `tests/` dir, pytest. Sample statements are synthesized with PyMuPDF inside test fixtures (no real statements in the repo, ever). Run with `python -m pytest tests/ -v`.

**Commit convention:** One commit per task, `feat:`/`fix:` prefixes, end with
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

## Phase 0 — Instrumentation (measure before building)

### Task 0.1: Stop silently defaulting to AMEX; record undetected providers

**Files:**
- Modify: `provider_config.py:77-90` (`detect_provider`)
- Modify: `app.py:1229-1245` (`process_single_file` detection block)
- Create: `tests/test_detection.py`

`detect_provider(pdf_text)` must return `None` when no marker matches (keep 'amex'/'barclaycard' logic as-is). Callers decide the fallback.

In `process_single_file`: when `provider == 'auto'`, run detection over first-page text. If detection returns `None`:
1. Append one line to `unrecognized_uploads.txt`: ISO date + comma + `unknown` (NO statement content — privacy rule: never log document text).
2. Fall back to the existing AMEX path (current behavior preserved).
3. Thread a `detected: bool` back so `/redact`'s JSON response includes `"provider_detected": true/false` and `"provider": "<name>"`.

Tests: synthesize 3 tiny PDFs with fitz (one containing "American Express", one "Barclaycard", one "Monzo Bank") → assert `detect_provider` returns `amex_uk`, `barclaycard`, `None` respectively.

### Task 0.2: Bank-request capture + GA events

**Files:**
- Modify: `app.py` (template JS around line 1103-1150, `/redact` handler)

Frontend, in the upload success/failure handler:
- `gtag('event', 'redact_success', {provider: resp.provider, detected: resp.provider_detected})`
- `gtag('event', 'redact_error')` on failure
- When `provider_detected === false`, show a non-blocking banner under the results: "We couldn't confidently detect your bank. Which bank is this statement from?" with a small text input + button POSTing to `/bank-request` (JSON `{bank: "<user text>"}`), then `gtag('event', 'bank_request', {bank})`.

Backend: `POST /bank-request` appends sanitized bank name (strip, max 60 chars, alphanumeric+spaces only) + ISO date to `bank_requests.txt`, returns `{ok: true}`.

### Task 0.3: Token-gated stats endpoint

**Files:**
- Modify: `app.py`

`GET /stats?token=<STATS_TOKEN>`: returns 404 unless env `STATS_TOKEN` is set and matches. Returns JSON: usage counter value, line-count of `unrecognized_uploads.txt`, tail-20 of `bank_requests.txt`. Read files defensively (missing → 0/[]).

---

## Phase 1 — Repositioning, trust, SEO

### Task 1.1: Fix false claims + repositioned hero/meta copy

**Files:**
- Modify: `app.py` template — `<title>` (line ~41), meta description (~42), hero eyebrow (~890-894), hero h1/p (~894+)

Exact copy (do not improvise):
- Title: `Redact Statements — Share Bank & Card Statements Without Oversharing`
- Meta description: `Blackout every transaction on your AMEX or Barclaycard statement except the ones you choose. For rental applications and expense claims. True redaction — text is destroyed, not hidden. Files deleted after download.`
- Eyebrow: `True redaction · Files deleted after download` (replaces the false `Privacy-first · Runs locally`)
- H1: `Share your statement.\nNot your whole life.`
- Hero paragraph: `Landlords and employers only need to see certain transactions. Upload your statement, choose what stays visible, and every other transaction is permanently blacked out — the text underneath is destroyed, not just covered.`

Remove the Visa/Mastercard claim from the meta description. No mention of visas or mortgages anywhere (recipients reject redacted statements there).

### Task 1.2: Trust section

**Files:**
- Modify: `app.py` template — insert a section between "How it works" and the form card, matching the existing card CSS patterns (reuse `.how-card`-style classes; add minimal CSS in the existing `<style>` block, matching the current design system and both themes).

Three cards:
1. **True redaction** — "We use PDF redaction annotations that destroy the text underneath. Copy-paste and text extraction find nothing — unlike drawing black boxes, which can be reversed."
2. **Nothing is kept** — "Your statement is processed in memory on our server and the file is deleted immediately after you download it. We never read, store, or log your transactions."
3. **No account needed** — "No signup, no email, no tracking of who you are. Upload, redact, download, done."

Honesty rule: it IS server-side. Never write "local", "on your device", or "in your browser".

### Task 1.3: SEO guide pages + sitemap + robots

**Files:**
- Create: `guides.py` — `GUIDES` dict: slug → {title, meta_description, html_body}
- Modify: `app.py` — register `GET /guides/<slug>` (render with a minimal shared layout reusing the site header/footer/theme), `GET /sitemap.xml`, `GET /robots.txt`, and a "Guides" link in the site footer.

Four guides (600–900 words each, plain honest prose, each ending with a CTA linking to `/`):
1. `do-landlords-accept-redacted-bank-statements` — answer: generally yes if name, balance, and income stay visible; what to keep vs. hide; UK-flavored.
2. `redact-bank-statement-for-rental-application` — step-by-step with this tool; what a "landlord-safe" statement shows.
3. `redact-amex-statement-for-expense-claims` — keep only reimbursable merchants; employer acceptance.
4. `why-black-boxes-fail-pdf-redaction` — drawn rectangles/preview markup are recoverable; what true redaction is.
Each guide must include one honest note: mortgage underwriters and UK visa applications generally require unredacted statements — don't use redaction there.

`sitemap.xml`: homepage + 4 guide URLs, absolute URLs from env `BASE_URL` (default `https://pdf-redact.onrender.com`). `robots.txt`: allow all + sitemap line.

---

## Phase 2 — Provider groundwork (bank statements, not just cards)

### Task 2.1: Generic UK bank statement parser (beta)

**Files:**
- Create: `redact_bank_generic.py`
- Modify: `provider_config.py` — add `generic_bank_uk` config; `detect_provider` returns it when text matches r'\b(sort code|paid in|paid out|money in|money out)\b' (case-insensitive) and no card provider matched
- Modify: `app.py` `process_single_file` — route `generic_bank_uk` to the new parser
- Create: `tests/test_bank_generic.py`

Parser contract (mirror `redact_barclaycard.py`'s span-grouping approach):
- Row model: date (`\d{1,2}\s+\w{3}` or `\d{2}/\d{2}/\d{4}` or `\d{2}/\d{2}/\d{2}`), description spans, and 1–3 amount columns (paid-in / paid-out / balance).
- Group spans into transaction rows by Y-coordinate (tolerance 3pt), same as barclaycard's approach.
- Whitelist logic identical to existing: keep row if any keyword (case-insensitive substring) matches description; else add redaction rect over date+description+paid-in/paid-out cells. NEVER redact the balance column or non-transaction page furniture (headers, footers, name).
- Return `(output_path, total_redacted_amount, kept_rows)` like `redact_barclaycard`.

Tests: fixture builds a synthetic 1-page "bank statement" PDF with fitz (header "Sort Code 12-34-56", column headers Date/Description/Paid out/Paid in/Balance, 6 rows with known text at known positions). Assert: keyword row survives (text still extractable), non-keyword rows' description text is gone after redaction, balance column text all still present.

Label in dropdown as "Other UK bank (beta)" — add to `get_all_providers()`.

### Task 2.2: Beta banner for generic path

**Files:**
- Modify: `app.py`

When the generic bank parser ran, include `"beta": true` in `/redact` response; frontend shows: "Bank statement support is in beta — please check every page of the output before sharing it." (Always good advice; mandatory messaging for beta path.)

Real per-bank configs (Monzo, HSBC, NatWest…) are added later, one config each, driven by `bank_requests.txt` demand data + real sample statements from the user. NOT in this plan — do not invent bank-specific configs without samples.

---

## Phase 3 — Scenario presets

### Task 3.1: Landlord mode (backend)

**Files:**
- Modify: `app.py` (`/redact` accepts `mode` param: `custom` (default, current behavior) | `landlord`)
- Modify: `redact_barclaycard.py`, `redact_bank_generic.py` — accept `keep_credits: bool = False` kwarg
- Create: `tests/test_landlord_mode.py`

Landlord mode = keep credits (money in / CR-suffixed / negative amounts — reuse the existing CR handling from commit 9408e1f) AND keyword-matched rows (keywords optional in this mode); redact all other debits. Balance column always visible (generic parser already guarantees this). In landlord mode the frontend explains: "Keeps: money coming in (salary, transfers), your balances, and any transactions you whitelist (e.g. rent). Hides: all other spending."

For AMEX/Barclaycard (credit cards) landlord mode makes little sense — if `mode == 'landlord'` and provider is a card, return a 400 with a friendly message: "Landlord mode is for bank statements. For card statements, use Expense mode with keywords."

Tests: synthetic bank PDF with 2 credit rows, 4 debit rows, 1 debit matching keyword "rent" → landlord mode keeps 2 credits + rent row + all balances, destroys other 3 debit descriptions.

### Task 3.2: Mode selector (frontend)

**Files:**
- Modify: `app.py` template

Radio-card group above the keyword input, matching existing design system, three options:
- **Expense claim** (default): "Keep only transactions matching your keywords." (= current behavior, `mode=custom`)
- **Landlord / rental**: "Keep income, balances, and rent. Hide other spending." (`mode=landlord`; keyword field becomes optional with placeholder "rent, letting agent (optional)")
- **Custom**: same as expense but neutral copy.
Selected mode POSTs as `mode` field. GA event `mode_selected`.

---

## Phase 4 — Payments scaffold (feature-flagged)

### Task 4.1: Stripe Checkout behind env flag

**Files:**
- Create: `payments.py`
- Modify: `app.py`, `requirements.txt` (add `stripe`, `itsdangerous`)
- Create: `tests/test_payments.py`

All payment behavior is OFF unless env `PAYMENTS_ENABLED=1` AND `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` + `STRIPE_PRICE_SINGLE` + `STRIPE_PRICE_PACK5` are set. When off, the app behaves exactly as today (free, unlimited) — assert this in tests.

When on:
- Credits live in a signed cookie (`itsdangerous.URLSafeSerializer` with `SECRET_KEY` env): `{"credits": n}`. Every visitor starts with 1 free credit per cookie (honest wording: "First document free").
- `/redact` decrements a credit; at 0 credits returns 402 JSON `{"error": "no_credits", "buy_url": "/buy"}`; frontend shows paywall card: "£2.99 for one document · £9.99 for five. No account needed."
- `GET /buy?pack=single|pack5` → creates Stripe Checkout Session (mode=payment, the matching price ID, `success_url=/paid?session_id={CHECKOUT_SESSION_ID}`, `cancel_url=/`), redirects.
- `GET /paid` → verifies the Checkout Session with Stripe API is `payment_status == "paid"` and not already-consumed (keep a `consumed_sessions.txt` file of session IDs), then sets credits cookie (+1 or +5) and redirects to `/` with a success toast.
- `POST /stripe-webhook` → verify signature, log `checkout.session.completed` to `payments_log.txt` (session id + amount only). Cookie granting happens on `/paid`; webhook is the audit trail.

Tests (no network): flag-off behavior unchanged; cookie serializer round-trips; `/redact` with 0-credit cookie and flag on → 402; consumed-session file prevents double-grant (mock the Stripe call).

**Blocked on user (do not attempt):** creating the Stripe account, adding real keys to Render, setting prices live. Scaffold must run green in test mode with fake env values.

---

## Execution notes

- Phases run sequentially (0→4); tasks within a phase touch overlapping regions of `app.py`, so no parallel edits to it.
- After each phase: run `python -m pytest tests/ -v`, boot the app (`python app.py`), exercise the changed flow in a browser, commit.
- Reviewer (manager) checks per phase: no false privacy claims introduced, no visa/mortgage positioning, no statement content ever written to logs/counters, redaction still uses true redaction annotations (`apply_redactions`).
