"""
Silent Mirror — Model Router

Tries Groq (Llama 3.3) first. If Groq raises a rate-limit / quota /
token-limit error, automatically retries the same conversation through
Gemini instead. Both providers are normalized to return a single plain
string, so the rest of app.py never needs to know which one answered.

Requires GEMINI_API_KEY set alongside GROQ_API_KEY in Railway.
"""

import os
from groq import Groq, APIStatusError as GroqAPIStatusError
from google import genai
from google.genai import types as genai_types

groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-2.0-flash"

# Status codes/messages that mean "this provider is out of capacity right
# now" as opposed to a real bug worth surfacing as an error.
RATE_LIMIT_STATUS_CODES = {429, 503}


def _is_fallback_worthy_error(exc: Exception) -> bool:
    """
    True for failures that mean 'this provider can't answer right now'
    — rate limits, quota, AND the known gpt-oss empty-completion bug
    (reasoning budget exhausted before any visible content). False for
    genuine bugs that should surface as errors, not get silently masked
    by switching providers.
    """
    status = getattr(exc, "status_code", None)
    if status in RATE_LIMIT_STATUS_CODES:
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in [
        "rate limit", "quota", "token limit", "resource_exhausted",
        "empty completion",  # matches the RuntimeError raised in _call_groq
    ])


def get_completion(
    system_prompt: str,
    messages: list,
    max_tokens: int = 250
) -> tuple[str, str]:
    """
    Returns (reply_text, provider_used).

    Tries Gemini first; falls back to Groq only on
    rate-limit/quota-style failures.
    """

    try:
        # Primary provider
        return _call_gemini(
            system_prompt,
            messages,
            max_tokens
        ), "gemini"

    except Exception as e:
        # Only fallback for errors that are safe to retry
        if not _is_fallback_worthy_error(e):
            raise

        try:
            # Fallback provider
            return _call_groq(
                system_prompt,
                messages,
                max_tokens
            ), "groq"

        except Exception as groq_error:
            # Both providers failed
            raise RuntimeError(
                f"Both providers failed. "
                f"Gemini: {e} | Groq: {groq_error}"
            ) from groq_error


def get_completion(
    system_prompt: str,
    messages: list,
    max_tokens: int = 250
) -> tuple[str, str]:
    """
    Returns (reply_text, provider_used).

    Tries Gemini first; falls back to Groq only on
    rate-limit/quota-style failures.
    """

    try:
        return _call_gemini(system_prompt, messages, max_tokens), "gemini"

    except Exception as e:
        if not _is_fallback_worthy_error(e):
            raise  # real bug — don't mask it

        try:
            return _call_groq(system_prompt, messages, max_tokens), "groq"

        except Exception as groq_error:
            raise RuntimeError(
                f"Both providers failed. "
                f"Gemini: {e} | Groq: {groq_error}"
            ) from groq_error