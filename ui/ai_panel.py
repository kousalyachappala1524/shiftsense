"""Claude calls from the UI: explicit button only, cached by payload SHA-256, per-session limit."""

from __future__ import annotations

import hashlib

import streamlit as st

from engine import claude_brief as cb


def calls_per_session() -> int:
    try:
        return int(cb.setting("AI_CALLS_PER_SESSION", "5"))
    except ValueError:
        return 5


@st.cache_resource
def _known_hashes() -> set:
    """Hashes whose answers are already cached (shared across sessions)."""
    return set()


@st.cache_data(ttl=7 * 24 * 3600, max_entries=256, show_spinner=False)
def _cached(kind: str, key_hash: str, _fn, _args: tuple) -> str:
    return _fn(*_args)


SAVED_CAPTION = ("Saved example: written by Claude (Opus 5.5) on 26 Nov 2026 from this exact demo data. "
                 "This public demo doesn't call the API, so nothing was generated just now.")
LIVE_CAPTION = "Written by Claude just now from the verified numbers"
TEMPLATE_CAPTION = "Template built from the verified numbers (no AI)"


def caption_for(source: str) -> str:
    return {"claude": LIVE_CAPTION, "cached": LIVE_CAPTION, "saved": SAVED_CAPTION}.get(source, TEMPLATE_CAPTION)


def generate(kind: str, payload: dict, fn, *args, extra: str = "",
             question: str | None = None) -> tuple[str | None, str, str | None]:
    """Returns (text, source, error). source: claude | cached | saved | none | limit | error.

    Without an API key, a saved example is returned when one was written for this exact payload."""
    api_key = cb.get_api_key()
    if not api_key:
        saved = cb.saved_example(kind, payload, question)
        return (saved, "saved", None) if saved else (None, "none", None)
    h = hashlib.sha256((kind + "|" + cb.payload_hash(payload) + "|" + extra).encode()).hexdigest()
    if h in _known_hashes():
        try:
            return _cached(kind, h, fn, (api_key, *args)), "cached", None
        except cb.AIError as e:
            return None, "error", str(e)
    st.session_state.setdefault("ai_calls", 0)
    if st.session_state["ai_calls"] >= calls_per_session():
        return None, "limit", ("You've used this session's AI requests. The template is shown instead; "
                               "refresh the page later to start a new session.")
    st.session_state["ai_calls"] += 1
    try:
        text = _cached(kind, h, fn, (api_key, *args))
        _known_hashes().add(h)
        return text, "claude", None
    except cb.AIError as e:
        return None, "error", str(e)


def status_banner() -> bool:
    """Shows whether Claude is on. Returns True if a key is configured."""
    used = st.session_state.get("ai_calls", 0)
    if cb.get_api_key():
        st.success(f"Claude is on (model {cb.model_id()}). {max(calls_per_session() - used, 0)} new AI requests left "
                   "in this session; repeated requests are served from cache.", icon="✅")
        return True
    st.info("**Demo mode: no live AI connection.** This public demo doesn't call the Claude API. For the built-in demo "
            "data you'll see saved examples Claude wrote from these exact numbers; for anything else, a template built "
            "from the same verified figures. Owners switch on live Claude by adding `ANTHROPIC_API_KEY` to the app's "
            "secrets.", icon="💡")
    return False
