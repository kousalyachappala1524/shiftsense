"""Claude writes words, Python does the maths.

Claude only ever receives a compact JSON payload of figures that were already computed
and tested in engine/ (target: under 1,500 tokens). It never sees raw rota files and is
told never to calculate or invent numbers. Every call is triggered by an explicit button,
cached by the SHA-256 of the payload (see ui/ai_panel.py), and has a template fallback.

The API key is read only from Streamlit secrets or the ANTHROPIC_API_KEY env var.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache

import numpy as np
import pandas as pd

from .demo_data import DAY_LABELS
from .kpis import inr

DEFAULT_MODEL = "claude-opus-5-5"
TOKEN_BUDGET = 1500


class AIError(Exception):
    """User-facing message for any failed Claude call."""


# ------------------------------------------------------------------ settings

def setting(name: str, default: str | None = None) -> str | None:
    """Streamlit secrets first, then environment variables. Never logged."""
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # no secrets file / not running under Streamlit
        pass
    return os.environ.get(name, default)


def get_api_key() -> str | None:
    return setting("ANTHROPIC_API_KEY") or None


def model_id() -> str:
    return setting("CLAUDE_MODEL", DEFAULT_MODEL)


# ------------------------------------------------------------------ payload helpers

def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return str(o)


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default, ensure_ascii=False)


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


SAVED_EXAMPLES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "sample_data", "saved_ai_examples.json")


@lru_cache(maxsize=1)
def _saved_examples() -> dict:
    try:
        with open(SAVED_EXAMPLES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {(e["kind"], e["payload_sha256"], e.get("question")): e["text"] for e in data.get("entries", [])}


def saved_example(kind: str, payload: dict, question: str | None = None) -> str | None:
    """A saved Claude-written example for exactly this payload (demo data), or None."""
    return _saved_examples().get((kind, payload_hash(payload), question))


def saved_questions(kind: str, payload: dict) -> list[str]:
    h = payload_hash(payload)
    return [q for (k, ph, q) in _saved_examples() if k == kind and ph == h and q]


def approx_tokens(payload: dict) -> int:
    """Conservative estimate: about 3.5 characters per token for compact JSON."""
    return int(len(canonical_json(payload)) / 3.5) + 1


def _r(x, nd=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return round(float(x), nd)


# ------------------------------------------------------------------ UK payload

def build_verified_payload(week_start, rates, totals: dict, sites: pd.DataFrame, flags: pd.DataFrame,
                           emp_week: pd.DataFrame, wins: dict, overtime_premium: float) -> dict:
    """Everything the UK brief may say, already computed. Nothing else is sent."""
    order = {"breach": 0, "warning": 1, "info": 2}
    f = flags.sort_values("severity", key=lambda c: c.map(order), kind="stable") if not flags.empty else flags
    issues = [{"who": r["employee_name"], "site": r["site"], "rule": r["rule"], "severity": r["severity"],
               "when": r["when"], "detail": r["detail"]} for _, r in f.head(10).iterrows()]
    nmw = [{"who": r["employee_name"], "paid_rate": _r(r["hourly_rate"]), "minimum": r["min_rate"],
            "band": r["min_rate_name"]} for _, r in emp_week[emp_week["nmw_breach"]].iterrows()]
    return {
        "currency": "GBP",
        "week_commencing": f"{pd.Timestamp(week_start):%d %b %Y}",
        "rate_table": rates.version,
        "overtime_premium_policy": overtime_premium,
        "totals": {k: (_r(v) if isinstance(v, float) else v) for k, v in totals.items()},
        "sites": [{"site": r["site"], "paid_hours": _r(r["paid_hours"], 1), "wage_bill": _r(r["gross_pay"]),
                   "employer_ni": _r(r["employer_ni"]), "loaded_cost": _r(r["loaded_cost"]),
                   "sales": _r(r["sales"]), "labour_cost_pct": _r(r["labour_cost_pct"])} for _, r in sites.iterrows()],
        "compliance": {
            "breaches": int((flags["severity"] == "breach").sum()) if not flags.empty else 0,
            "warnings": int((flags["severity"] == "warning").sum()) if not flags.empty else 0,
            "nmw_breaches": nmw,
            "issues": issues,
        },
        "fixes_before_publishing": [{"who": x["who"], "action": x["action"],
                                     "cost_change_week": x["cost_change_week"]} for x in wins["compliance_fixes"][:8]],
        "top_savings": [{"action": a["detail"], "saving_week": a["saving_week"], "ni_part": a["ni_saving_week"]}
                        for a in wins["cost_actions"][:3]],
        "total_saving_week": wins["weekly_saving"],
        "total_saving_month": wins["monthly_saving"],
    }


UK_SYSTEM = """You write the Weekly Operations & Compliance Brief for a UK retail operations director.
You receive a JSON payload of figures that were calculated and checked by deterministic software.

Hard rules:
- Use ONLY numbers that appear in the JSON, exactly as given. Never calculate, total, round differently,
  convert or estimate any figure. If a figure you'd like isn't in the JSON, leave it out.
- Never invent people, sites, shifts, laws or rates. Name people and shifts exactly as in the JSON.
- Money is GBP (£). Write plainly for a busy operations director; no preamble.

Format (Markdown, max ~350 words), exactly these sections:
#### Executive summary
(3 bullets)
#### Legal & Working Time breaches to fix before publishing the rota
(each breach: who, when, what, and the fix from fixes_before_publishing)
#### Employer NI & margin savings
(the top_savings actions with their £/week, then total_saving_week and total_saving_month)
#### Next week watch-outs
(2-3 bullets drawn only from the data)"""


def uk_brief(api_key: str, payload: dict, client=None) -> str:
    msg = ("Verified payload (JSON):\n<payload>\n" + canonical_json(payload) + "\n</payload>\n\n"
           "Write this week's brief.")
    return call_claude(api_key, UK_SYSTEM, [{"role": "user", "content": msg}], effort="medium", client=client)


def uk_fallback_brief(payload: dict) -> str:
    """Template brief used when no key is configured or the API is unavailable."""
    t, c = payload["totals"], payload["compliance"]

    def gbp(x):
        return "n/a" if x is None else f"£{x:,.2f}"

    def n(count, word, plural=None):
        return f"{count} {word if count == 1 else (plural or word + 's')}"

    lines = ["#### Executive summary",
             f"- Week commencing {payload['week_commencing']}: {t.get('headcount')} staff, "
             f"{t.get('paid_hours')} paid hours, loaded labour cost {gbp(t.get('loaded_cost'))}"
             + (f" ({t['labour_cost_pct']}% of sales)." if t.get("labour_cost_pct") is not None else "."),
             f"- {n(c['breaches'], 'Working Time breach', 'Working Time breaches')} and {n(c['warnings'], 'warning')} "
             "to clear before the rota is published"
             + (f", plus {n(len(c['nmw_breaches']), 'minimum wage breach', 'minimum wage breaches')}."
                if c["nmw_breaches"] else "."),
             f"- Quick wins worth {gbp(payload['total_saving_week'])} a week ({gbp(payload['total_saving_month'])} a month).",
             "", "#### Legal & Working Time breaches to fix before publishing the rota"]
    breaches = [i for i in c["issues"] if i["severity"] == "breach"]
    lines += [f"- **{i['who']}** ({i['site']}, {i['when']}): {i['rule']}. {i['detail']}." for i in breaches] or ["- None found."]
    lines += [f"- Minimum wage: **{n['who']}** is paid £{n['paid_rate']:.2f}; the {n['band']} is £{n['minimum']:.2f}."
              for n in c["nmw_breaches"]]
    if payload["fixes_before_publishing"]:
        lines += ["", "Fixes:"] + [f"{k}. {x['action']}" for k, x in enumerate(payload["fixes_before_publishing"], 1)]
    lines += ["", "#### Employer NI & margin savings"]
    lines += [f"- {a['action']} Saves {gbp(a['saving_week'])} a week (Employer NI part: {gbp(a['ni_part'])})."
              for a in payload["top_savings"]] or ["- No overtime shifts can move without breaking a rule this week."]
    lines += [f"- Total: {gbp(payload['total_saving_week'])} a week, {gbp(payload['total_saving_month'])} a month.",
              "", "#### Next week watch-outs",
              f"- Employer NI this week is {gbp(t.get('employer_ni'))}: keep extra hours with staff who have spare "
              "contracted hours before paying overtime.",
              f"- Day-one SSP exposure if every employee were off sick for a week: {gbp(t.get('ssp_exposure_one_week_all_staff'))}.",
              "", "_Template brief. Add an Anthropic API key for a brief written by Claude._"]
    return "\n".join(lines)


# ------------------------------------------------------------------ India payload (planner)

INDIA_SYSTEM = """You are ShiftSense, a workforce planning analyst for a multi-store fashion retailer in India.
You help store and HR operations managers understand next week's demand forecast, staffing plan (rota),
coverage, labour cost and Labour Codes working-hours checks.

Rules:
- Use only the JSON plan data supplied in the conversation. If the answer is not in the data, say what is missing.
- Never calculate new numbers: quote figures exactly as they appear in the data.
- Quote specific days, hours and people. Money is in Indian rupees (₹); use lakh (L) for large values.
- Be direct and practical: lead with the answer, then the evidence, then a recommended action.
- Keep answers under 180 words unless asked for more. Use short bullet points where they help.
- The data is a planning forecast, not actuals. Do not invent employees, events or figures."""

INDIA_REPORT_PROMPT = """Write the weekly workforce management report for this store-week from the plan data.
Format in Markdown with exactly these sections:
#### Headline  (2 sentences: the week in one line + the single most important action)
#### Demand outlook  (footfall and sales vs a normal week, events, peak days/hours)
#### Staffing & coverage  (coverage %, where and when we are short or over-staffed, key-holder issues)
#### Cost & compliance  (labour cost %, overtime: who and why, Labour Codes findings)
#### Actions for the store manager  (3 numbered, specific actions with day/time and expected effect)
Keep it under 350 words. Plain English for a busy operations manager."""


def build_india_payload(store_name: str, week_label: str, plan: dict, fcst: dict, rules: dict,
                        compliance: dict | None = None, scenario: dict | None = None) -> dict:
    k = plan["kpis"]
    rota = plan["rota"]
    req, sch = rota["required"], rota["scheduled"]
    o = plan["open_hour"]
    gap = req - sch
    short = sorted(({"day": DAY_LABELS[d], "hour": f"{o + h:02d}:00", "short_by": int(gap[d, h])}
                    for d, h in zip(*np.where(gap > 0))), key=lambda x: -x["short_by"])[:10]
    staff = rota["staff"]
    notable = staff[(staff["overtime_hours"] > 0) | (staff["scheduled_hours"] < staff["contract_hours"] - 4)]
    bt = fcst["backtest"]
    prof, level = fcst["model"]["profile"], fcst["model"]["level"]
    daily = plan["daily"][["Day", "Event", "Footfall", "Sales (INR)", "Required hrs", "Scheduled hrs",
                           "Short hrs", "Over hrs", "Labour cost %"]]
    payload = {
        "store": store_name,
        "week": week_label,
        "rules": {k2: rules[k2] for k2 in ("customers_per_staff_hour", "min_staff", "max_shift_hours",
                                           "overtime_multiplier")},
        "kpis": {kk: (round(v, 1) if isinstance(v, float) else v) for kk, v in k.items()
                 if kk != "schedule_efficiency_pct"},
        "normal_week": {"footfall": round(float(prof["footfall"].sum() * level)),
                        "sales_inr": round(float((prof["footfall"] * prof["spv"]).sum() * level))},
        "daily": daily.to_dict(orient="records"),
        "short_slots_top10": short,
        "rota_alerts": rota["alerts"][:6],
        "notable_staff": [{"name": r["name"], "role": r["role"], "contract": r["contract_hours"],
                           "scheduled": r["scheduled_hours"], "overtime": r["overtime_hours"]}
                          for _, r in notable.head(10).iterrows()],
        "forecast_error_pct": {"daily_footfall_mape": round(bt["mape_daily_footfall"], 1),
                               "daily_sales_mape": round(bt["mape_daily_sales"], 1)},
    }
    if compliance:
        payload["labour_codes"] = compliance
    if scenario:
        payload["what_if"] = scenario
    return payload


def data_message(ctx: dict) -> str:
    return "Plan data for this store-week (JSON):\n<plan_data>\n" + canonical_json(ctx) + "\n</plan_data>"


def india_report(api_key: str, ctx: dict, client=None) -> str:
    msgs = [{"role": "user", "content": data_message(ctx) + "\n\n" + INDIA_REPORT_PROMPT}]
    return call_claude(api_key, INDIA_SYSTEM, msgs, effort="medium", client=client)


def india_ask(api_key: str, ctx: dict, history: list[dict], question: str, client=None) -> str:
    msgs = [{"role": "user", "content": data_message(ctx)},
            {"role": "assistant", "content": "Got it. I have the plan data for this store-week. What would you like to know?"}]
    msgs += history[-8:]
    msgs.append({"role": "user", "content": question})
    return call_claude(api_key, INDIA_SYSTEM, msgs, effort="medium", client=client)


def india_offline_report(ctx: dict) -> str:
    """Rule-based report used when no API key is configured."""
    k = ctx["kpis"]
    daily = pd.DataFrame(ctx["daily"])
    ff_vs = 100 * (k["forecast_footfall"] / max(ctx["normal_week"]["footfall"], 1) - 1)
    peak = daily.sort_values("Footfall", ascending=False).iloc[0]
    worst = daily.sort_values("Short hrs", ascending=False).iloc[0]
    events = sorted({e for e in daily["Event"] if e})
    staff = (pd.DataFrame(ctx["notable_staff"]) if ctx["notable_staff"]
             else pd.DataFrame(columns=["name", "overtime", "scheduled", "contract"]))
    ot = staff[staff["overtime"] > 0].sort_values("overtime", ascending=False)
    under = staff[staff["scheduled"] < staff["contract"] - 4]
    lc = ctx.get("labour_codes") or {}
    lines = [
        "#### Headline",
        ("Forecast footfall is in line with a normal week" if abs(ff_vs) < 1.5
         else f"Forecast footfall is {ff_vs:+.0f}% vs a normal week") + f", with {inr(k['forecast_sales_inr'])} sales; "
        f"the rota covers {k['coverage_pct']:.0f}% of required hours at {k['labour_cost_pct']:.1f}% labour cost. "
        + (f"Priority: close the {worst['Short hrs']} short hours on {worst['Day']}." if worst["Short hrs"] > 0
           else "Priority: hold the plan and trim idle hours."),
        "",
        "#### Demand outlook",
        f"- Peak day: **{peak['Day']}** ({peak['Footfall']:,} visitors, {inr(peak['Sales (INR)'])})."
        + (f" Events this week: {', '.join(events)}." if events else " No events this week."),
        "",
        "#### Staffing & coverage",
        f"- Coverage **{k['coverage_pct']:.1f}%**; {k['understaffed_hours']:.0f} staff-hours short, "
        f"{k['overstaffed_hours']:.0f} over.",
    ]
    keyholder = [a for a in ctx["rota_alerts"] if "key-holder" in a]
    by_size = sorted((a for a in ctx["rota_alerts"] if "key-holder" not in a),
                     key=lambda a: -int(a.split(": ")[1].split(" ")[0]))
    lines += [f"- {a}" for a in (keyholder + by_size)[:4]]
    lines += [
        "",
        "#### Cost & compliance",
        f"- Labour cost **{inr(k['labour_cost_inr'])}** ({k['labour_cost_pct']:.1f}% of sales); "
        f"sales per labour hour {inr(k['sales_per_labour_hour_inr'])}.",
        f"- Overtime {k['overtime_hours']:.0f} hrs"
        + (f" (top: {', '.join(f'{r.name} {r.overtime:.0f}h' for r in ot.head(3).itertuples())})." if len(ot) else "."),
    ]
    if lc:
        lines.append(f"- Labour Codes ({lc['state']}): {lc['breaches']} breach(es), {lc['warnings']} warning(s), "
                     f"{lc['women_night_shift_reminders']} women's late shifts needing written consent and transport records.")
    lines += ["", "#### Actions for the store manager"]
    actions = []
    if worst["Short hrs"] > 0:
        when = next((a.split("(", 1)[1].rstrip(")") for a in ctx["rota_alerts"]
                     if a.startswith(f"{worst['Day']}:") and "(" in a), "the short hours")
        actions.append(f"Close the {worst['Short hrs']} short staff-hours on {worst['Day']} ({when}) with "
                       "part-time or flexible cover, or by moving a shift start/finish.")
    if lc.get("women_night_shift_reminders"):
        actions.append("Collect written consent and confirm safe transport for every woman working after 19:00.")
    if len(under) and len(ot):
        actions.append(f"Move hours from overtime to under-used contracts ({', '.join(under['name'].head(3))}).")
    if k["overstaffed_hours"] > 20:
        actions.append(f"Put the {k['overstaffed_hours']:.0f} over-covered staff-hours to work: replenishment, "
                       "visual merchandising and training in quiet hours, or review part-time contracts.")
    actions.append("Re-check the plan on Thursday against the latest footfall and adjust weekend shifts.")
    lines += [f"{i}. {a}" for i, a in enumerate(actions[:3], 1)]
    lines += ["", "_Rule-based draft. Add an Anthropic API key to get an AI-written report._"]
    return "\n".join(lines)


# ------------------------------------------------------------------ API call

def _client(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)


def call_claude(api_key: str, system: str, messages: list[dict], effort: str = "medium", client=None) -> str:
    """One Messages API call. Returns Markdown, or raises AIError with a plain-English message."""
    import anthropic

    client = client or _client(api_key)
    try:
        resp = client.beta.messages.create(
            model=model_id(),
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if the main model declines, the API retries on a fallback model
            output_config={"effort": effort},
            system=system,
            messages=messages,
        )
    except anthropic.APITimeoutError:
        raise AIError("Claude took too long to answer, so the template is shown instead. Try again in a minute.")
    except anthropic.AuthenticationError:
        raise AIError("The Anthropic API key was rejected. Check the key in your app secrets.")
    except anthropic.PermissionDeniedError:
        raise AIError("This API key does not have access to the model. Check your Anthropic Console settings.")
    except anthropic.NotFoundError:
        raise AIError(f"Model '{model_id()}' was not found for this key. Set CLAUDE_MODEL to a model you can use.")
    except anthropic.RateLimitError:
        raise AIError("Rate limit reached. Wait a minute and try again.")
    except anthropic.BadRequestError as e:
        raise AIError(f"The request was rejected: {e.message}")
    except anthropic.APIStatusError as e:
        raise AIError(f"Anthropic API error ({e.status_code}). Try again shortly.")
    except anthropic.APIConnectionError:
        raise AIError("Could not reach the Anthropic API. Check the internet connection.")

    if resp.stop_reason == "refusal":
        raise AIError("Claude declined this request. Try rephrasing.")
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if resp.stop_reason == "max_tokens":
        text += "\n\n_(Answer was cut short.)_"
    return text or "_No answer returned._"
