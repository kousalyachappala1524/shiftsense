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


def generate(kind: str, payload: dict, fn, *args, extra: str = "") -> tuple[str | None, str, str | None]:
    """Returns (text, source, error). source: claude | cached | none | limit | error."""
    api_key = cb.get_api_key()
    if not api_key:
        return None, "none", None
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
    st.info("**Claude is off: no Anthropic API key is set.** You'll see a template built from the same verified "
            "numbers. To switch Claude on, add `ANTHROPIC_API_KEY` to the app's Streamlit secrets or environment.",
            icon="🔌")
    return False
