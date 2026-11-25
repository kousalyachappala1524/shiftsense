"""Deterministic "Cost & Compliance Quick Wins" for a UK rota.

Cost moves
  Only OVERTIME hours move: a whole shift is offered to a colleague when every hour of it
  is above the donor's contracted hours, so nobody is pushed below their contract.
  Colleagues are ranked by role match, then spare contracted hours, then fewest hours this
  week (fair distribution). Age and NI status are never used to pick who gets hours.
  A move is kept only if (a) it saves money once pay, overtime premium, holiday accrual and
  Employer NI are recalculated for both people, (b) it creates no new Working Time issue
  for the colleague, and (c) a contracted colleague stays within their contract.
  The colleague is assumed to be available on a day they aren't rostered: confirm first.

Compliance fixes
  For each breach, the cheapest legal fix we can compute: hand a clopening shift to an
  eligible colleague (or start it later), lengthen a too-short break, correct a pay rate.
"""

from __future__ import annotations

import pandas as pd

from config.uk_rates import DEFAULT_RATES, WTR, UKRates, WTRLimits

from . import uk_cost, wtr_rules

FIELDS_FROM_EMPLOYEE = ["employee_id", "employee_name", "age_band", "age", "contract_type", "hourly_rate",
                        "contracted_hours", "wtr_48h_opt_out", "gender"]
FLOOR_WORDS = ("advisor", "sales", "assistant", "associate", "team member", "crew", "server", "barista")


def compatible(role_a: str, role_b: str) -> bool:
    a, b = str(role_a).lower(), str(role_b).lower()
    if a == b:
        return True
    manager = lambda r: "manager" in r
    floor = lambda r: any(w in r for w in FLOOR_WORDS) and not manager(r)
    return floor(a) and floor(b)


def _emp_profile(shifts: pd.DataFrame, emp: str) -> pd.Series:
    return shifts[shifts["employee_id"] == emp].iloc[0]


def _issue_count(shifts: pd.DataFrame, emp: str, limits: WTRLimits) -> int:
    f = wtr_rules.check_shifts(shifts[shifts["employee_id"] == emp], limits)
    return int((f["severity"].isin([wtr_rules.BREACH, wtr_rules.WARNING])).sum()) if not f.empty else 0


def _cost_of(shifts: pd.DataFrame, emps: list[str], rates: UKRates, premium: float) -> float:
    c = uk_cost.weekly_costs(shifts[shifts["employee_id"].isin(emps)], rates, premium)
    return float(c["loaded_cost"].sum()) if not c.empty else 0.0


def _reassign(shifts: pd.DataFrame, idx, recipient: pd.Series) -> pd.DataFrame:
    out = shifts.copy()
    for f in FIELDS_FROM_EMPLOYEE:
        out.at[idx, f] = recipient[f]
    return out


def _hours(shifts: pd.DataFrame, emp: str) -> float:
    return float(shifts.loc[shifts["employee_id"] == emp, "paid_hours"].sum())


def _ranked_recipients(shifts: pd.DataFrame, shift: pd.Series, exclude: str) -> list[pd.Series]:
    """Colleagues at the same site with a compatible role, fairest first. Never ranked by age or NI."""
    site_staff = shifts[(shifts["site"] == shift["site"]) & (shifts["employee_id"] != exclude)]
    cands = []
    for emp, g in site_staff.groupby("employee_id"):
        p = g.iloc[0]
        if not compatible(p["role"], shift["role"]):
            continue
        hours = _hours(shifts, emp)
        spare = (float(p["contracted_hours"]) - hours) if pd.notna(p["contracted_hours"]) and p["contracted_hours"] else None
        cands.append((p["role"] != shift["role"], -(spare if spare is not None else 0), hours, emp, p))
    cands.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return [c[4] for c in cands]


def _fits(trial: pd.DataFrame, before: pd.DataFrame, rec: pd.Series, limits: WTRLimits) -> bool:
    emp = rec["employee_id"]
    if pd.notna(rec["contracted_hours"]) and rec["contracted_hours"] and \
            _hours(trial, emp) > float(rec["contracted_hours"]) + 1e-9:
        return False
    if rec["contract_type"] == "irregular_zero_hours" and _hours(trial, emp) > limits.adult_weekly_hours_limit:
        return False
    return _issue_count(trial, emp, limits) <= _issue_count(before, emp, limits)


def _fmt_shift(s: pd.Series) -> str:
    return f"{s['start']:%a %d %b} {s['start']:%H:%M}–{s['end']:%H:%M}"


def quick_wins(shifts: pd.DataFrame, rates: UKRates = DEFAULT_RATES, overtime_premium: float = 1.5,
               limits: WTRLimits = WTR) -> dict:
    work = shifts.copy()
    cost_actions = []

    # ---- 1. overtime shifts -> colleagues with spare hours
    costs = uk_cost.weekly_costs(work, rates, overtime_premium)
    donors = costs[costs["overtime_hours"] > 0].sort_values("overtime_hours", ascending=False)
    for _, d in donors.iterrows():
        emp = d["employee_id"]
        while True:
            ot_now = _hours(work, emp) - float(d["contracted_hours"])
            mine = work[work["employee_id"] == emp]
            options = mine[mine["paid_hours"] <= ot_now + 1e-9].sort_values("paid_hours", ascending=False)
            moved = False
            for idx, sh in options.iterrows():
                for rec in _ranked_recipients(work, sh, emp):
                    trial = _reassign(work, idx, rec)
                    if not _fits(trial, work, rec, limits):
                        continue
                    pair = [emp, rec["employee_id"]]
                    saving = _cost_of(work, pair, rates, overtime_premium) - _cost_of(trial, pair, rates, overtime_premium)
                    if saving <= 0.5:
                        continue
                    ni_before = uk_cost.weekly_costs(work[work["employee_id"].isin(pair)], rates, overtime_premium)["employer_ni"].sum()
                    ni_after = uk_cost.weekly_costs(trial[trial["employee_id"].isin(pair)], rates, overtime_premium)["employer_ni"].sum()
                    cost_actions.append({
                        "type": "Move an overtime shift",
                        "site": sh["site"],
                        "when": _fmt_shift(sh),
                        "from": f"{sh['employee_name']} ({ot_now:.1f}h over contract)",
                        "to": rec["employee_name"],
                        "hours": round(float(sh["paid_hours"]), 2),
                        "saving_week": uk_cost.money(saving),
                        "ni_saving_week": uk_cost.money(ni_before - ni_after),
                        "detail": (f"Give {sh['employee_name']}'s {_fmt_shift(sh)} shift at {sh['site']} to "
                                   f"{rec['employee_name']}: removes {sh['paid_hours']:.1f} overtime hours paid at "
                                   f"{overtime_premium:g}×, within {rec['employee_name']}'s contract and rest rules."),
                    })
                    work = trial
                    moved = True
                    break
                if moved:
                    break
            if not moved:
                break

    # ---- 2. compliance fixes (on the original rota, so each fix stands alone)
    fixes = []
    flags = wtr_rules.check_shifts(shifts, limits)
    by_row = shifts.set_index("row")
    for _, f in flags[flags["severity"] == wtr_rules.BREACH].iterrows():
        rows = [int(r) for r in str(f["rows"]).replace(" ", "").split(",") if r.isdigit()]
        if f["rule"] == "Daily rest (clopening)" and len(rows) == 2:
            later = by_row.loc[rows[1]]
            idx = shifts.index[shifts["row"] == rows[1]][0]
            fix = None
            for rec in _ranked_recipients(shifts, later, later["employee_id"]):
                trial = _reassign(shifts, idx, rec)
                if _fits(trial, shifts, rec, limits) and _issue_count(trial, later["employee_id"], limits) < \
                        _issue_count(shifts, later["employee_id"], limits):
                    pair = [later["employee_id"], rec["employee_id"]]
                    delta = _cost_of(trial, pair, rates, overtime_premium) - _cost_of(shifts, pair, rates, overtime_premium)
                    fix = {"type": "Fix clopening", "who": f["employee_name"], "when": _fmt_shift(later),
                           "action": f"Give the {_fmt_shift(later)} shift at {later['site']} to {rec['employee_name']}",
                           "cost_change_week": uk_cost.money(delta)}
                    break
            if fix is None:
                prev = by_row.loc[rows[0]]
                need = limits.young_daily_rest_hours if f["age_band"] == "16-17" else limits.adult_daily_rest_hours
                new_start = prev["end"] + pd.Timedelta(hours=need)
                lost = (new_start - later["start"]).total_seconds() / 3600
                fix = {"type": "Fix clopening", "who": f["employee_name"], "when": _fmt_shift(later),
                       "action": f"Start {f['employee_name']}'s shift at {new_start:%H:%M} instead of "
                                 f"{later['start']:%H:%M} and cover the first {lost:.1f}h with someone else",
                       "cost_change_week": 0.0}
            fixes.append(fix)
        elif f["rule"] == "Rest break" and rows:
            s = by_row.loc[rows[0]]
            need = limits.young_min_break_mins if s["age_band"] == "16-17" else limits.adult_min_break_mins
            extra = need - s["unpaid_break_mins"]
            fixes.append({"type": "Fix rest break", "who": f["employee_name"], "when": _fmt_shift(s),
                          "action": f"Give {f['employee_name']} a {need}-minute break on {_fmt_shift(s)} "
                                    f"({extra:.0f} more minutes, unpaid)",
                          "cost_change_week": uk_cost.money(-extra / 60 * float(s["hourly_rate"]))})
        elif f["rule"] == "Overlapping shifts":
            fixes.append({"type": "Fix double booking", "who": f["employee_name"], "when": f["when"],
                          "action": "Remove one of the two overlapping shifts", "cost_change_week": 0.0})
        elif f["rule"] in ("Young worker daily hours", "Young worker weekly hours", "Weekly rest"):
            fixes.append({"type": f"Fix {f['rule'].lower()}", "who": f["employee_name"], "when": f["when"],
                          "action": f"Reduce {f['employee_name']}'s hours: {f['detail']}", "cost_change_week": 0.0})

    costs0 = uk_cost.weekly_costs(shifts, rates, overtime_premium)
    for _, c in costs0[costs0["nmw_breach"]].iterrows():
        back = c["nmw_shortfall_per_hour"] * c["paid_hours"]
        fixes.append({"type": "Fix minimum wage", "who": c["employee_name"], "when": f"Week of {c['week_start']:%d %b}",
                      "action": f"Raise {c['employee_name']}'s rate from £{c['hourly_rate']:.2f} to "
                                f"£{c['min_rate']:.2f} ({c['min_rate_name']}) and check back pay",
                      "cost_change_week": uk_cost.money(back)})

    weekly = uk_cost.money(sum(a["saving_week"] for a in cost_actions))
    return {
        "cost_actions": sorted(cost_actions, key=lambda a: -a["saving_week"]),
        "compliance_fixes": fixes,
        "weekly_saving": weekly,
        "monthly_saving": uk_cost.money(weekly * uk_cost.WEEKS_PER_MONTH),
        "optimised_shifts": work,
        "assumptions": ("Colleagues are assumed available on days they aren't rostered. Only overtime hours move, "
                        "and hours are never allocated by age."),
    }
