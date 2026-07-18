"""
Silent Mirror — Auth (v1, personal-use tier)

Single shared secret, checked in ONE place via Flask's before_request hook.
This is intentionally NOT full multi-user auth (no password hashing, no
sessions, no per-user accounts) — that's over-engineering for a solo,
non-public deployment. This closes the actual current gap: anyone who
finds the URL and guesses/knows a user_id can currently read or write
that user's private data with zero proof of identity.

HARD GATE: before this app is shared with even one other person, this
must be upgraded to real per-user auth (hashed passwords + sessions, or
OAuth). Do not skip that step when this stops being personal-only.
"""

import os
import secrets
from functools import wraps
from flask import request, jsonify

# Set this in your environment (Railway/local .env), never hardcode it.
SHARED_SECRET = os.environ.get("SM_SHARED_SECRET")

# Routes that don't require the secret (health checks, static index page)
PUBLIC_PATHS = {"/", "/routes"}


def check_auth():
    """
    Called from a single before_request hook in app.py.
    Returns None if the request is authorized, or a Flask response
    (401) if it is not. Centralizing this means no future route can
    forget to add the check individually.
    """
    if request.path in PUBLIC_PATHS:
        return None

    if not SHARED_SECRET:
        # Fail closed, not open — if the secret isn't configured,
        # refuse everything rather than silently allowing all requests.
        return jsonify({"error": "Server auth not configured"}), 500

    provided = request.headers.get("X-SM-Auth", "")

    # constant-time comparison — avoids leaking the secret via timing
    if not secrets.compare_digest(provided, SHARED_SECRET):
        return jsonify({"error": "Unauthorized"}), 401

    return None
