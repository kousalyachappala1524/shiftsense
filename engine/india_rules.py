"""India working-hours checks (Labour Codes) on a rota built by ShiftSense.

Works on the planner's rota for one store-week. Rest intervals: shifts longer than
5 hours are assumed to include a 1-hour unpaid rest interval (the legal minimum is
30 minutes after 5 hours of work), so "working hours" = shift length - rest interval.
"""

from __future__ import annotations

import pandas as pd

from config import india_rates as IR
from config.india_rates import IndiaPreset

BREACH, WARNING, REMINDER = "breach", "warning", "reminder"


def rota_to_shifts(rota_shifts: pd.DataFrame, staff: pd.DataFrame, week_start: pd.Timestamp,
                   tz_name: str = IR.TIMEZONE) -> pd.DataFrame:
    """Planner rota (day index + whole hours) -> timezone-aware shifts with working hours."""
    if rota_shifts.empty:
        return pd.DataFrame()
    s = rota_shifts.merge(staff[["staff_id", "gender", "hourly_cost_inr", "contract_hours"]]
                          if "gender" in staff else staff[["staff_id", "hourly_cost_inr", "contract_hours"]],
                          on="staff_id", how="left")
    if "gender" not in s:
        s["gender"] = ""
    base = pd.Timestamp(week_start).tz_localize(tz_name) if pd.Timestamp(week_start).tz is None else pd.Timestamp(week_start)
    s["start_ts"] = [base + pd.Timedelta(days=int(d), hours=int(h)) for d, h in zip(s["day_idx"], s["start"])]
    s["end_ts"] = [base + pd.Timedelta(days=int(d), hours=int(h)) for d, h in zip(s["day_idx"], s["end"])]
    s["rest_interval_h"] = (s["hours"] > IR.REST_INTERVAL_AFTER_HOURS) * IR.REST_INTERVAL_HOURS
    s["working_hours"] = s["hours"] - s["rest_interval_h"]
    return s


def check(shifts: pd.DataFrame, preset: IndiaPreset) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (findings, per-person summary, totals)."""
    cols = ["name", "role", "rule", "severity", "when", "detail", "shifts"]
    if shifts.empty:
        return pd.DataFrame(columns=cols), pd.DataFrame(), {}
    findings, people = [], []
    for sid, g in shifts.groupby("staff_id"):
        first = g.iloc[0]
        daily_excess = 0.0
        for _, r in g.iterrows():
            when = f"{r['day']} {r['start']:02d}:00–{r['end']:02d}:00"
            if r["working_hours"] > preset.daily_hours + 1e-9:
                excess = r["working_hours"] - preset.daily_hours
                daily_excess += excess
                findings.append({"name": r["name"], "role": r["role"], "rule": "Daily working hours", "severity": WARNING,
                                 "when": when, "detail": f"{r['working_hours']:.1f}h of work vs {preset.daily_hours:g}h "
                                 f"daily limit in {preset.name}: {excess:.1f}h counts as overtime (needs consent)"})
        late = g[(g["end"] > IR.WOMEN_NIGHT_START_HOUR) | (g["start"] < IR.WOMEN_NIGHT_END_HOUR)]
        if str(first.get("gender", "")).upper().startswith("F") and len(late):
            findings.append({"name": first["name"], "role": first["role"], "rule": "Women's night work",
                             "severity": REMINDER,
                             "when": ", ".join(f"{d} to {e:02d}:00" for d, e in zip(late["day"], late["end"])),
                             "detail": f"{len(late)} shift(s) after 19:00 or before 06:00: needs her written consent "
                                       "and the employer's safety and transport arrangements on record",
                             "shifts": len(late)})
        week_work = float(g["working_hours"].sum())
        weekly_excess = max(0.0, week_work - preset.weekly_hours)
        ot = max(daily_excess, weekly_excess)
        q_ot = ot * IR.WEEKS_PER_QUARTER
        days = int(g["day_idx"].nunique())
        if days > 7 - IR.WEEKLY_DAYS_OFF:
            findings.append({"name": first["name"], "role": first["role"], "rule": "Weekly day off", "severity": BREACH,
                             "when": "This week", "detail": f"Works {days} days; at least one day off in seven is required"})
        if weekly_excess > 0:
            findings.append({"name": first["name"], "role": first["role"], "rule": "Weekly hours", "severity": WARNING,
                             "when": "This week", "detail": f"{week_work:.1f}h of work vs {preset.weekly_hours:g}h: "
                             f"{weekly_excess:.1f}h overtime needs written consent and is paid at 2×"})
        if q_ot > preset.ot_quarter_cap:
            findings.append({"name": first["name"], "role": first["role"], "rule": "Quarterly overtime cap", "severity": BREACH,
                             "when": "If repeated for 13 weeks", "detail": f"{ot:.1f}h overtime a week = {q_ot:.0f}h a quarter, "
                             f"over the {preset.ot_quarter_cap:g}h cap in {preset.name}"})
        rate = float(first.get("hourly_cost_inr") or 0)
        people.append({"name": first["name"], "role": first["role"], "days": days,
                       "working_hours": round(week_work, 1), "overtime_hours": round(ot, 1),
                       "quarter_ot_if_repeated": round(q_ot, 0),
                       "overtime_pay_inr": round(ot * rate * IR.OT_PAY_MULTIPLIER),
                       "night_shifts_women": int(((g["end"] > IR.WOMEN_NIGHT_START_HOUR) | (g["start"] < IR.WOMEN_NIGHT_END_HOUR)).sum())
                       if str(first.get("gender", "")).upper().startswith("F") else 0})
    f = pd.DataFrame(findings, columns=cols)
    order = {BREACH: 0, WARNING: 1, REMINDER: 2}
    if not f.empty:
        f = f.sort_values("severity", key=lambda c: c.map(order), kind="stable").reset_index(drop=True)
    p = pd.DataFrame(people)
    totals = {
        "state": preset.name,
        "daily_limit_hours": preset.daily_hours,
        "weekly_limit_hours": preset.weekly_hours,
        "ot_quarter_cap_hours": preset.ot_quarter_cap,
        "breaches": int((f["severity"] == BREACH).sum()) if not f.empty else 0,
        "warnings": int((f["severity"] == WARNING).sum()) if not f.empty else 0,
        "women_night_work_rule": "work after 19:00 or before 06:00 needs written consent and safe transport",
        "women_night_shift_reminders": int(f.loc[f["rule"] == "Women's night work", "shifts"].sum()) if not f.empty else 0,
        "women_needing_consent": int((f["rule"] == "Women's night work").sum()) if not f.empty else 0,
        "overtime_hours": round(float(p["overtime_hours"].sum()), 1),
        "overtime_pay_inr": int(p["overtime_pay_inr"].sum()),
        "staff_over_quarter_cap": int((p["quarter_ot_if_repeated"] > preset.ot_quarter_cap).sum()),
    }
    return f, p, totals
