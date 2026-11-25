"""Saved Claude examples (used when the public demo runs without an API key) must stay truthful.

1. They must belong to the payload the app produces today (same SHA-256), so they never show
   next to numbers they weren't written from.
2. Every figure in them must exist in that verified payload (zero maths drift).
"""

import json
import os
import re
from pathlib import Path

import pytest

from engine import claude_brief as cb

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "sample_data" / "saved_ai_examples.json").read_text(encoding="utf-8"))


def numbers_in(obj) -> list[float]:
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", cb.canonical_json(obj).replace(",", ""))]


@pytest.fixture(scope="module")
def app_payloads():
    """Run the real Streamlit app headless and capture the payloads it would send to Claude."""
    from streamlit.testing.v1 import AppTest

    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240)
        at.run()
        assert not at.exception
        india = at.session_state["in_ctx"]
        at.session_state["mode"] = "uk"
        at.run()
        at.button(key="uk_demo_main").click().run()
        assert not at.exception
        return {"india": india, "uk": at.session_state["uk_payload"]}
    finally:
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_examples_match_what_the_app_sends_today(app_payloads):
    for e in DATA["entries"]:
        assert cb.payload_hash(app_payloads[e["payload"]]) == e["payload_sha256"], (
            f"{e['kind']} example is stale: the app's {e['payload']} payload changed. Rewrite the example.")
    assert cb.saved_example("uk_brief", app_payloads["uk"])
    assert cb.saved_example("india_report", app_payloads["india"])
    assert len(cb.saved_questions("india_ask", app_payloads["india"])) == 3


def test_stored_payload_snapshots_hash_correctly():
    for e in DATA["entries"]:
        assert cb.payload_hash(DATA["payloads"][e["payload"]]) == e["payload_sha256"]


def test_no_example_for_other_data():
    changed = json.loads(json.dumps(DATA["payloads"]["uk"]))
    changed["total_saving_week"] = 1.0
    assert cb.saved_example("uk_brief", changed) is None


def test_uk_example_has_zero_money_drift():
    payload = DATA["payloads"]["uk"]
    verified = {round(abs(x), 2) for x in numbers_in(payload)}
    text = next(e["text"] for e in DATA["entries"] if e["kind"] == "uk_brief")
    amounts = [float(a.replace(",", "")) for a in re.findall(r"£([\d,]+\.\d{2})", text)]
    assert len(amounts) >= 15
    for a in amounts:
        assert round(a, 2) in verified, f"£{a:.2f} is not in the verified payload"


@pytest.mark.parametrize("entry", [e for e in DATA["entries"] if e["payload"] == "india"],
                         ids=lambda e: e["kind"] + (":" + e["question"][:20] if e["question"] else ""))
def test_india_examples_quote_only_payload_numbers(entry):
    payload = DATA["payloads"]["india"]
    verified = numbers_in(payload)
    text = entry["text"]
    for lakh in re.findall(r"₹([\d.]+) L", text):  # lakh formatting of a verified rupee amount
        value = float(lakh) * 100_000
        step = 10 ** -(len(lakh.split(".")[1]) if "." in lakh else 0) * 100_000
        assert any(abs(v - value) <= step / 2 + 1 for v in verified), f"₹{lakh} L"
    rest = re.sub(r"₹[\d.]+ L", "", text).replace(",", "")
    for n in re.findall(r"\d+(?:\.\d+)?", rest):
        x = float(n)
        assert any(abs(v - x) <= 0.51 for v in verified), f"{n} in the {entry['kind']} example is not in the payload"
