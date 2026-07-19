"""Payments + signed-credit-cookie module (Phase 4, Task 4.1).

ALL payment behaviour is OFF unless:

    PAYMENTS_ENABLED=1
    AND every one of STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
        STRIPE_PRICE_SINGLE, STRIPE_PRICE_PACK5 is set.

When ``payments_enabled()`` is False, app.py skips every payment code path —
no cookie is read or written and ``stripe`` is never imported — so the app runs
exactly as it does today (free, unlimited). The ``stripe`` package is imported
*lazily* inside the enabled-only wrappers below, so a missing ``stripe`` package
can never raise an ImportError in the default (disabled) state — not even at
app startup.

Credits live in a signed cookie (``itsdangerous.URLSafeSerializer`` keyed on the
``SECRET_KEY`` env var): ``{"credits": n}``. Each visitor starts with one free
credit (``FREE_CREDITS`` — honest wording: "First document free"). The signed
cookie means a credit tally cannot be forged without the key; it carries no
statement content or PII, only a small integer.

Consumed Checkout Sessions are tracked in ``consumed_sessions.txt`` so a paid
session can grant credits at most once (the double-grant guard). The Stripe
webhook writes an audit line to ``payments_log.txt`` (session id + amount only —
never statement content).
"""
import os
import threading

from itsdangerous import URLSafeSerializer, BadSignature

# --- configuration --------------------------------------------------------

COOKIE_NAME = 'redact_credits'

# Monetization is on only when explicitly enabled AND fully configured.
_REQUIRED_STRIPE_VARS = (
    'STRIPE_SECRET_KEY',
    'STRIPE_WEBHOOK_SECRET',
    'STRIPE_PRICE_SINGLE',
    'STRIPE_PRICE_PACK5',
)

# Every visitor starts here — "First document free".
FREE_CREDITS = 1

# Credits granted by each paid pack.
PACK_CREDITS = {'single': 1, 'pack5': 5}

# File-backed records (gitignored runtime state).
CONSUMED_SESSIONS_FILE = 'consumed_sessions.txt'

# --- secret key / serializer ---------------------------------------------
# The cookie is just a signed credit tally, not sensitive on its own, but in
# production set a real, secret ``SECRET_KEY`` (Render / Heroku config var) so
# credit counts cannot be forged. This default is for local dev only.
_DEFAULT_SECRET_KEY = 'dev-insecure-secret-key-CHANGE-ME'


def _secret_key():
    return os.environ.get('SECRET_KEY') or _DEFAULT_SECRET_KEY


def _serializer():
    return URLSafeSerializer(_secret_key(), salt='credits')


def _site_url():
    """Canonical site root for absolute Stripe redirect URLs."""
    return os.environ.get('BASE_URL', 'https://pdf-redact.onrender.com').rstrip('/')


# --- feature flag ---------------------------------------------------------

def payments_enabled():
    """True only when monetization is explicitly enabled AND fully configured."""
    if os.environ.get('PAYMENTS_ENABLED') != '1':
        return False
    return all(os.environ.get(name) for name in _REQUIRED_STRIPE_VARS)


# --- credits cookie -------------------------------------------------------

def get_credits(request):
    """Return the signed-credit count on the incoming request, or None when the
    cookie is absent / tampered / malformed. Never raises."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    try:
        data = _serializer().loads(cookie)
    except BadSignature:
        return None
    try:
        return int(data.get('credits'))
    except (TypeError, ValueError, AttributeError):
        return None


def set_credits_cookie(response, credits):
    """Stamp (overwrite) the signed credits cookie on a response. Returns it."""
    token = _serializer().dumps({'credits': int(credits)})
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        samesite='Lax',
        max_age=60 * 60 * 24 * 365,  # 1 year
    )
    return response


# --- consumed-session guard (prevents double-grant) -----------------------

_CONSUMED_LOCK = threading.Lock()


def _read_consumed_locked(path):
    """Read the set of already-consumed session ids. Caller holds the lock."""
    try:
        with open(path) as f:
            return {line.split(',', 1)[0].strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()
    except Exception:
        return set()


def claim_session(session_id):
    """Atomically claim a Checkout Session for credit-granting.

    Returns True if this session may still grant credits (and is now recorded as
    consumed), False if it has already been consumed. The check-and-mark happen
    under one lock so two concurrent ``/paid`` hits for the same session cannot
    both grant.
    """
    path = CONSUMED_SESSIONS_FILE
    with _CONSUMED_LOCK:
        if session_id in _read_consumed_locked(path):
            return False
        try:
            with open(path, 'a') as f:
                f.write(session_id + '\n')
        except Exception:
            # If we cannot persist the claim, refuse to grant rather than risk a
            # double-grant on the next request.
            return False
        return True


def session_is_consumed(session_id):
    """Read-only check: has this session already granted credits?"""
    with _CONSUMED_LOCK:
        return session_id in _read_consumed_locked(CONSUMED_SESSIONS_FILE)


# --- Stripe wrappers (lazy import; monkeypatchable in tests) --------------
# Each wrapper imports ``stripe`` only when called, and only ever runs when
# payments are enabled. Tests replace these (e.g. ``payments.retrieve_session``)
# so no network call is made.

def _stripe():
    """Configure and return the stripe module (imported lazily)."""
    import stripe
    stripe.api_key = os.environ['STRIPE_SECRET_KEY']
    return stripe


def create_checkout_session(pack):
    """Create a Stripe Checkout Session for ``pack`` ('single' | 'pack5') and
    return it (has ``.url`` / ``.id``). Raises KeyError/ValueError for unknown
    packs."""
    if pack not in PACK_CREDITS:
        raise ValueError(f'unknown pack: {pack!r}')
    price_id = {
        'single': os.environ['STRIPE_PRICE_SINGLE'],
        'pack5': os.environ['STRIPE_PRICE_PACK5'],
    }[pack]
    base = _site_url()
    return _stripe().checkout.Session.create(
        mode='payment',
        line_items=[{'price': price_id, 'quantity': 1}],
        # {CHECKOUT_SESSION_ID} is a Stripe template literal substituted by Stripe.
        success_url=f'{base}/paid?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=f'{base}/',
        metadata={'pack': pack},
    )


def retrieve_session(session_id):
    """Fetch a Checkout Session from Stripe by id (has ``.payment_status`` /
    ``.metadata``)."""
    return _stripe().checkout.Session.retrieve(session_id)


def credits_for_session(session):
    """How many credits a paid Checkout Session grants (1 or 5), read from the
    ``pack`` metadata stamped at creation. Falls back to a single credit if the
    metadata is missing. Tolerates Stripe StripeObjects, plain dicts, and
    SimpleNamespace-style fakes used in tests."""
    meta = (session.get('metadata') if isinstance(session, dict)
            else getattr(session, 'metadata', None))
    pack = None
    if meta is not None:
        try:
            pack = meta.get('pack')          # dict and dict-like (StripeObject)
        except AttributeError:
            pack = getattr(meta, 'pack', None)
    return PACK_CREDITS.get(pack, PACK_CREDITS['single'])


def verify_webhook(payload, signature):
    """Validate a Stripe webhook signature against STRIPE_WEBHOOK_SECRET and
    return the parsed event. Raises on a bad signature."""
    return _stripe().Webhook.construct_event(
        payload, signature, os.environ['STRIPE_WEBHOOK_SECRET'])
