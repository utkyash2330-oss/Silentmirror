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


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in RATE_LIMIT_STATUS_CODES:
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in ["rate limit", "quota", "token limit", "resource_exhausted"])


def _call_groq(system_prompt: str, messages: list, max_tokens: int) -> str:
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system_prompt}, *messages]
    )
    return response.choices[0].message.content


def _call_gemini(system_prompt: str, messages: list, max_tokens: int) -> str:
    # Gemini uses role "model" instead of "assistant", and a
    # contents-of-parts structure instead of OpenAI-style messages.
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append(genai_types.Content(
            role=role,
            parts=[genai_types.Part(text=m["content"])]
        ))

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens
        )
    )
    return response.text


def get_completion(system_prompt: str, messages: list, max_tokens: int = 250) -> tuple[str, str]:
    """
    Returns (reply_text, provider_used) so callers/logs can tell which
    model actually answered. Tries Groq first; falls back to Gemini only
    on rate-limit/quota-style failures, not on every error (a real bug in
    the request should surface, not silently retry on a different model).
    """
    try:
        return _call_groq(system_prompt, messages, max_tokens), "groq"
    except (GroqAPIStatusError, Exception) as e:
        if not _is_rate_limit_error(e):
            raise  # real bug — don't mask it by silently switching providers
        try:
            return _call_gemini(system_prompt, messages, max_tokens), "gemini"
        except Exception as gemini_error:
            # both providers down — let this raise up to the /chat route,
            # which should return a clear error rather than a fake reply
            raise RuntimeError(
                f"Both providers failed. Groq: {e} | Gemini: {gemini_error}"
            ) from gemini_error
