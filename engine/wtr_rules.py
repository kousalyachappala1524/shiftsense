"""UK Working Time Regulations checks on a canonical shift table.

Every check works on timezone-aware timestamps, so overnight shifts and clock changes
are measured in real elapsed hours. Rest gaps are measured per employee across ALL
sites, so "closes Site A at 22:00, opens Site B at 07:00" is caught.

Severity:
  breach  - the rota as published would break the rule
  warning - needs a check (e.g. 48 hours is legally a 17-week average; adults may take
            weekly rest as 48 hours per fortnight instead of 24 per week)
  info    - worth knowing (night shifts)
"""

from __future__ import annotations

import pandas as pd

from config.uk_rates import WTR, WTRLimits

from .uk_cost import iso_week_start

BREACH, WARNING, INFO = "breach", "warning", "info"
FLAG_COLUMNS = ["employee_id", "employee_name", "site", "age_band", "rule", "severity", "when", "detail",
                "rows", "week_start"]


def _young(band: str) -> bool:
    return band == "16-17"


def _fmt(ts: pd.Timestamp) -> str:
    return ts.strftime("%a %d %b %H:%M")


def _hours(a: pd.Timestamp, b: pd.Timestamp) -> float:
    return (b - a).total_seconds() / 3600  # pandas subtracts in absolute (UTC) time


def night_hours(start: pd.Timestamp, end: pd.Timestamp, night_start: int = 23, night_end: int = 6) -> float:
    """Hours of a shift that fall inside night time (default 23:00-06:00 local)."""
    tz = start.tz
    total = 0.0
    day = (start - pd.Timedelta(days=1)).normalize()
    while day <= end.normalize():
        n0 = pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=night_start, tz=tz)
        nxt = day + pd.Timedelta(days=1)
        n1 = pd.Timestamp(year=nxt.year, month=nxt.month, day=nxt.day, hour=night_end, tz=tz)
        lo, hi = max(start, n0), min(end, n1)
        if hi > lo:
            total += _hours(lo, hi)
        day = nxt
    return total


def check_shifts(shifts: pd.DataFrame, limits: WTRLimits = WTR) -> pd.DataFrame:
    """Return one row per finding (breach / warning / info)."""
    flags: list[dict] = []
    if shifts.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)

    def add(r, rule, sev, when, detail, rows, wk=None):
        flags.append({"employee_id": r["employee_id"], "employee_name": r["employee_name"], "site": r["site"],
                      "age_band": r["age_band"], "rule": rule, "severity": sev, "when": when, "detail": detail,
                      "rows": rows, "week_start": wk if wk is not None else iso_week_start(r["start"])})

    for emp, g in shifts.sort_values(["employee_id", "start"]).groupby("employee_id", sort=False):
        g = g.reset_index(drop=True)
        young = _young(g.loc[0, "age_band"])
        # 1. rest breaks inside each shift
        for _, r in g.iterrows():
            worked = r["paid_hours"]
            after, need = ((limits.young_break_after_hours, limits.young_min_break_mins) if young
                           else (limits.adult_break_after_hours, limits.adult_min_break_mins))
            if worked > after and r["unpaid_break_mins"] < need:
                add(r, "Rest break", BREACH, _fmt(r["start"]),
                    f"{worked:.1f}h worked with a {r['unpaid_break_mins']:.0f}-min break; "
                    f"{'under-18s need 30 min after 4.5h' if young else 'adults need 20 min after 6h'}",
                    str(r["row"]))
            nh = night_hours(r["start"], r["end"], limits.night_start_hour, limits.night_end_hour)
            if young:
                young_night = night_hours(r["start"], r["end"], 22, 6)
                if young_night > 0:
                    add(r, "Young worker at night", WARNING, _fmt(r["start"]),
                        f"{young_night:.1f}h between 22:00 and 06:00. Under-18s generally can't work at night; "
                        "check whether a sector exception applies", str(r["row"]))
            elif nh >= limits.night_min_hours:
                add(r, "Night shift", INFO, _fmt(r["start"]),
                    f"{nh:.1f}h between 23:00 and 06:00: track as night work (8-hour average limit, health assessment)",
                    str(r["row"]))
            if young and worked > limits.young_daily_hours_limit:
                add(r, "Young worker daily hours", BREACH, _fmt(r["start"]),
                    f"{worked:.1f}h worked; under-18s can work at most 8 hours a day", str(r["row"]))

        # 2. daily rest between consecutive shifts, across all sites
        need_rest = limits.young_daily_rest_hours if young else limits.adult_daily_rest_hours
        for i in range(1, len(g)):
            prev, cur = g.loc[i - 1], g.loc[i]
            gap = _hours(prev["end"], cur["start"])
            if gap < 0:
                add(cur, "Overlapping shifts", BREACH, _fmt(cur["start"]),
                    f"Rostered at {prev['site']} until {prev['end']:%H:%M} and at {cur['site']} from "
                    f"{cur['start']:%H:%M} on the same day", f"{prev['row']}, {cur['row']}")
            elif gap < need_rest:
                where = (f"{prev['site']} → {cur['site']}" if prev["site"] != cur["site"] else prev["site"])
                add(cur, "Daily rest (clopening)", BREACH, _fmt(cur["start"]),
                    f"Only {gap:.1f}h rest between finishing {_fmt(prev['end'])} and starting {_fmt(cur['start'])} "
                    f"({where}); {'under-18s need 12h' if young else 'adults need 11h'}",
                    f"{prev['row']}, {cur['row']}")

        # 3. weekly rest and weekly hours, per calendar week (Mon-Sun, local time)
        g["week_start"] = g["start"].map(iso_week_start)
        for wk, w in g.groupby("week_start"):
            w = w.sort_values("start")
            first = w.iloc[0]
            win0 = wk
            win1 = wk + pd.Timedelta(days=7)
            cursor, longest = win0, 0.0
            for _, r in w.iterrows():
                longest = max(longest, _hours(cursor, max(cursor, r["start"])))
                cursor = max(cursor, r["end"])
            longest = max(longest, _hours(cursor, win1) if win1 > cursor else 0.0)
            need_weekly = limits.young_weekly_rest_hours if young else limits.adult_weekly_rest_hours
            if longest < need_weekly:
                add(first, "Weekly rest", BREACH if young else WARNING, f"Week of {wk:%d %b}",
                    f"Longest unbroken rest this week is {longest:.0f}h; "
                    + ("under-18s need 48h a week" if young
                       else "adults need 24h a week (or 48h a fortnight; check the next week)"),
                    ", ".join(str(x) for x in w["row"]), wk)
            hours = float(w["paid_hours"].sum())
            if young and hours > limits.young_weekly_hours_limit:
                add(first, "Young worker weekly hours", BREACH, f"Week of {wk:%d %b}",
                    f"{hours:.1f}h this week; under-18s can work at most 40 hours a week (no opt-out)",
                    ", ".join(str(x) for x in w["row"]), wk)
            elif not young and hours > limits.adult_weekly_hours_limit and not bool(first["wtr_48h_opt_out"]):
                add(first, "48-hour week", WARNING, f"Week of {wk:%d %b}",
                    f"{hours:.1f}h this week with no signed opt-out. The legal limit is a 48h average over "
                    "17 weeks; keep this week balanced by lighter ones", ", ".join(str(x) for x in w["row"]), wk)

    out = pd.DataFrame(flags, columns=FLAG_COLUMNS)
    order = {BREACH: 0, WARNING: 1, INFO: 2}
    if not out.empty:
        out = out.sort_values(by=["severity", "employee_name"], key=lambda c: c.map(order) if c.name == "severity" else c)
    return out.reset_index(drop=True)


def shift_flags(shifts: pd.DataFrame, flags: pd.DataFrame) -> pd.Series:
    """For the audit export: the rules each source row is involved in (semicolon-separated)."""
    by_row: dict[int, list[str]] = {}
    for _, f in flags.iterrows():
        for r in str(f["rows"]).split(","):
            r = r.strip()
            if r.isdigit():
                by_row.setdefault(int(r), []).append(f"{f['severity']}: {f['rule']}")
    return shifts["row"].map(lambda r: "; ".join(sorted(set(by_row.get(int(r), [])))))


def summary(flags: pd.DataFrame) -> dict:
    if flags.empty:
        return {"breaches": 0, "warnings": 0, "info": 0, "by_rule": {}}
    return {
        "breaches": int((flags["severity"] == BREACH).sum()),
        "warnings": int((flags["severity"] == WARNING).sum()),
        "info": int((flags["severity"] == INFO).sum()),
        "by_rule": {f"{rule} ({sev})": int(n) for (rule, sev), n in flags.groupby(["rule", "severity"]).size().items()},
    }
