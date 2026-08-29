"""
Silent Mirror — Auth (v3, configurable access mode)

Two modes, controlled by SM_ACCESS_MODE:

- "private" (DEFAULT — used if the var is unset): every route requires
  SM_SHARED_SECRET, same as the original single-user design. This is
  the safe default for anyone who clones this repo and deploys their
  own copy — you should have to deliberately opt into public access,
  not stumble into it.

- "public": only the two whole-database routes (/data/export-db,
  /data/import-db) require the secret. Ordinary use (chat, journal,
  insights) is open to any visitor. Only set this if you deliberately
  want a public-facing demo of your own deployment — see the README
  before enabling.

A single shared secret can't mean both "legitimate user" and "owner"
at once — that's why "public" mode narrows what the secret protects,
rather than trying to hide it from visitors who are meant to use the
app anyway.
"""

import os
import secrets
from flask import request, jsonify

SHARED_SECRET = os.environ.get("SM_SHARED_SECRET")
ACCESS_MODE = os.environ.get("SM_ACCESS_MODE", "private").strip().lower()

PUBLIC_PATHS_ALWAYS = {"/", "/routes"}  # never gated, in either mode
OWNER_ONLY_PATHS = {"/data/export-db", "/data/import-db"}  # always gated, in either mode


def _secret_matches(provided: str) -> bool:
    if not SHARED_SECRET:
        return False
    return secrets.compare_digest(provided, SHARED_SECRET)


def check_auth():
    """
    Called from a single before_request hook in app.py.
    Returns None if authorized, or a Flask response (401/500) if not.
    """
    if request.path in PUBLIC_PATHS_ALWAYS:
        return None

    if ACCESS_MODE == "public":
        needs_secret = request.path in OWNER_ONLY_PATHS
    else:
        needs_secret = True  # private mode: everything else is gated

    if not needs_secret:
        return None

    if not SHARED_SECRET:
        return jsonify({"error": "Server auth not configured"}), 500

    provided = request.headers.get("X-SM-Auth", "")
    if not _secret_matches(provided):
        return jsonify({"error": "Unauthorized"}), 401

    return None