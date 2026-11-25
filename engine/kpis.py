"""Workforce KPIs for one store-week."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .demo_data import DAY_LABELS


def week_kpis(fc_week: pd.DataFrame, rota: dict, uplift_pct: float = 0.0) -> dict:
    req, sch = rota["required"], rota["scheduled"]
    staff = rota["staff"]
    sales = float(fc_week["sales_inr"].sum()) * (1 + uplift_pct / 100)
    footfall = float(fc_week["footfall"].sum()) * (1 + uplift_pct / 100)
    sched_h = float(sch.sum())
    req_h = float(req.sum())
    covered = float(np.minimum(sch, req).sum())
    cost = float(staff["labour_cost_inr"].sum())
    contract = float(staff["contract_hours"].sum())
    return {
        "forecast_sales_inr": sales,
        "forecast_footfall": footfall,
        "required_hours": req_h,
        "scheduled_hours": sched_h,
        "labour_cost_inr": cost,
        "labour_cost_pct": 100 * cost / sales if sales else 0.0,
        "sales_per_labour_hour_inr": sales / sched_h if sched_h else 0.0,
        "contract_utilisation_pct": 100 * sched_h / contract if contract else 0.0,
        "schedule_efficiency_pct": 100 * covered / sched_h if sched_h else 0.0,
        "coverage_pct": 100 * covered / req_h if req_h else 0.0,
        "understaffed_hours": float(np.clip(req - sch, 0, None).sum()),
        "overstaffed_hours": float(np.clip(sch - req, 0, None).sum()),
        "overtime_hours": float(staff["overtime_hours"].sum()),
        "unused_contract_hours": float(np.clip(staff["contract_hours"] - staff["scheduled_hours"], 0, None).sum()),
        "headcount": int(len(staff)),
    }


def daily_table(fc_week: pd.DataFrame, rota: dict, uplift_pct: float = 0.0) -> pd.DataFrame:
    req, sch = rota["required"], rota["scheduled"]
    shifts = rota["shifts"]
    staff = rota["staff"].set_index("staff_id")
    g = fc_week.groupby("dow").agg(footfall=("footfall", "sum"), sales=("sales_inr", "sum"),
                                   event=("event", "first")).reindex(range(7))
    g[["footfall", "sales"]] = g[["footfall", "sales"]].fillna(0) * (1 + uplift_pct / 100)
    # daily cost: allocate each shift's hours at the person's rate (overtime premium spread over the week)
    day_cost = np.zeros(7)
    if not shifts.empty:
        for sid, grp in shifts.groupby("staff_id"):
            p = staff.loc[sid]
            avg_rate = p["labour_cost_inr"] / p["scheduled_hours"] if p["scheduled_hours"] else 0
            for _, s in grp.iterrows():
                day_cost[int(s["day_idx"])] += s["hours"] * avg_rate
    rows = []
    for d in range(7):
        sh = float(sch[d].sum())
        rows.append({
            "Day": DAY_LABELS[d],
            "Event": g.loc[d, "event"] if isinstance(g.loc[d, "event"], str) else "",
            "Footfall": round(g.loc[d, "footfall"]),
            "Sales (INR)": round(g.loc[d, "sales"]),
            "Required hrs": int(req[d].sum()),
            "Scheduled hrs": int(sh),
            "Short hrs": int(np.clip(req[d] - sch[d], 0, None).sum()),
            "Over hrs": int(np.clip(sch[d] - req[d], 0, None).sum()),
            "Labour cost (INR)": round(day_cost[d]),
            "Labour cost %": round(100 * day_cost[d] / g.loc[d, "sales"], 1) if g.loc[d, "sales"] else 0.0,
            "Sales / labour hr": round(g.loc[d, "sales"] / sh) if sh else 0,
        })
    return pd.DataFrame(rows)


def inr(x: float) -> str:
    """Indian-style money: 12.4 L, 1.2 Cr, 85,400."""
    x = float(x)
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:.2f} Cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:.1f} L"
    s = f"{abs(round(x)):,.0f}"
    # re-group thousands the Indian way (12,34,567)
    digits = s.replace(",", "")
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if x < 0 else "") + "₹" + s
