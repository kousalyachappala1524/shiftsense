import io
import json
import re
import zipfile
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

from config.uk_rates import DEFAULT_RATES
from engine import audit, claude_brief as cb, optimizer, uk_cost, uk_demo, wtr_rules


@pytest.fixture(scope="module")
def uk(demo_shifts):
    emp = uk_cost.weekly_costs(demo_shifts, overtime_premium=1.5)
    sites = uk_cost.site_costs(demo_shifts, emp, uk_demo.SITE_SALES)
    tot = uk_cost.totals(emp, sites)
    flags = wtr_rules.check_shifts(demo_shifts)
    wins = optimizer.quick_wins(demo_shifts, overtime_premium=1.5)
    payload = cb.build_verified_payload(emp["week_start"].iloc[0], DEFAULT_RATES, tot, sites, flags, emp, wins, 1.5)
    return {"emp": emp, "flags": flags, "payload": payload}


def test_payload_is_compact_and_stable(uk):
    p = uk["payload"]
    assert cb.approx_tokens(p) < cb.TOKEN_BUDGET
    assert cb.payload_hash(p) == cb.payload_hash(json.loads(cb.canonical_json(p)))
    assert "shifts" not in p and "rows" not in json.dumps(p)  # no raw rota rows are sent


def test_zero_math_drift_in_template_brief(uk):
    """Every £ figure in the brief must appear verbatim in the verified payload."""
    brief = cb.uk_fallback_brief(uk["payload"])
    verified = {round(float(n), 2) for n in re.findall(r"-?\d+(?:\.\d+)?", cb.canonical_json(uk["payload"]))}
    amounts = [float(a.replace(",", "")) for a in re.findall(r"£([\d,]+\.\d{2})", brief)]
    assert len(amounts) >= 8
    for value in amounts:
        assert round(value, 2) in verified, f"£{value:.2f} in the brief is not in the payload"


class FakeMessages:
    def __init__(self, stop="end_turn", exc=None):
        self.stop, self.exc, self.kw = stop, exc, None

    def create(self, **kw):
        self.kw = kw
        if self.exc:
            raise self.exc
        return NS(stop_reason=self.stop, content=[NS(type="thinking", thinking=""), NS(type="text", text="Brief")])


def test_request_shape_and_refusal(uk):
    fm = FakeMessages()
    assert cb.uk_brief("k", uk["payload"], client=NS(beta=NS(messages=fm))) == "Brief"
    kw = fm.kw
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["system"] == cb.UK_SYSTEM
    assert "never calculate" in cb.UK_SYSTEM.lower()
    with pytest.raises(cb.AIError, match="declined"):
        cb.uk_brief("k", uk["payload"], client=NS(beta=NS(messages=FakeMessages("refusal"))))


@pytest.mark.parametrize("exc,msg", [
    (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")), "too long"),
    (anthropic.AuthenticationError(message="x", response=httpx2.Response(401, request=httpx2.Request("POST", "https://a")), body=None), "rejected"),
    (anthropic.RateLimitError(message="x", response=httpx2.Response(429, request=httpx2.Request("POST", "https://a")), body=None), "Rate limit"),
])
def test_api_errors_become_plain_english(uk, exc, msg):
    with pytest.raises(cb.AIError, match=msg):
        cb.uk_brief("k", uk["payload"], client=NS(beta=NS(messages=FakeMessages(exc=exc))))


def test_api_key_comes_only_from_env_or_secrets(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cb.get_api_key() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-value")
    assert cb.get_api_key() == "test-value"


def test_audit_pack_contents(demo_shifts, uk):
    data = uk_demo.DEMO_FILE.read_bytes()
    brief = cb.uk_fallback_brief(uk["payload"])
    pack, meta = audit.build_pack(demo_shifts, uk["emp"], uk["flags"], brief, DEFAULT_RATES, data, "demo.csv", "template", 1.5)
    z = zipfile.ZipFile(io.BytesIO(pack))
    assert sorted(z.namelist()) == ["audit_metadata.json", "rota_audit_rows.csv", "weekly_management_brief.md"]
    m = json.loads(z.read("audit_metadata.json"))
    assert m["uk_rate_table_version"] == "2026-27" and m["input_sha256"] == audit.sha256_bytes(data)
    assert m["generated_at_utc"].endswith("Z") and m["findings"]["breaches"] == 4
    rows = z.read("rota_audit_rows.csv").decode()
    assert rows.count("\n") == len(demo_shifts) + 1 and "breach: Daily rest (clopening)" in rows
