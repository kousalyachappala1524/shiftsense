"""Deterministic UK labour cost engine: pay, Employer NI, holiday accrual, NMW checks.

All money is rounded to the penny with ROUND_HALF_UP at the point it is reported.
Nothing here is ever delegated to an LLM.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from config.uk_rates import DEFAULT_RATES, UKRates

WEEKS_PER_MONTH = 52 / 12


def money(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_ni_relief(age_band: str, contract_type: str, age: float | None) -> bool:
    """Employer NI relief to the Upper Secondary Threshold: under-21s, and apprentices under 25."""
    if age_band in ("16-17", "18-20"):
        return True
    if contract_type == "apprentice_u25" and (age is None or age < 25):
        return True
    return False


def employer_ni_weekly(gross: float, age_band: str = "21+", contract_type: str = "fixed",
                       age: float | None = None, rates: UKRates = DEFAULT_RATES, rounded: bool = True) -> float:
    """Class 1 secondary NI on one week's earnings for one employee."""
    threshold = (rates.ni_upper_secondary_threshold_weekly if is_ni_relief(age_band, contract_type, age)
                 else rates.ni_secondary_threshold_weekly)
    ni = rates.employer_ni_rate * max(0.0, gross - threshold)
    return money(ni) if rounded else ni


def minimum_rate(age_band: str, contract_type: str = "fixed", age: float | None = None,
                 rates: UKRates = DEFAULT_RATES) -> tuple[float, str]:
    """Statutory minimum hourly rate and its name.

    The apprentice rate applies to apprentices under 19, or 19+ in the first year of the
    apprenticeship. We can't see the apprenticeship start date, so 19+ apprentices are
    checked against the apprentice rate and the result is labelled for review.
    """
    if contract_type == "apprentice_u25":
        return rates.nmw_apprentice, "Apprentice rate"
    if age_band == "16-17":
        return rates.nmw_16_17, "Under-18 rate"
    if age_band == "18-20":
        return rates.nmw_18_20, "18-20 rate"
    return rates.nlw_21_plus, "National Living Wage (21+)"


def iso_week_start(ts: pd.Timestamp) -> pd.Timestamp:
    """Monday 00:00 (local) of the week a shift starts in."""
    d = ts.normalize()
    return d - pd.Timedelta(days=d.day_of_week)


def weekly_costs(shifts: pd.DataFrame, rates: UKRates = DEFAULT_RATES, overtime_premium: float = 1.0,
                 holiday_for_irregular: bool = True) -> pd.DataFrame:
    """One row per employee per week.

    overtime_premium: company policy multiplier for paid hours above contracted hours
    (UK law has no statutory overtime premium). 1.0 = overtime paid at the normal rate.
    """
    if shifts.empty:
        return pd.DataFrame()
    s = shifts.copy()
    s["week_start"] = s["start"].map(iso_week_start)
    s["base_pay_shift"] = s["paid_hours"] * s["hourly_rate"]
    rows = []
    for (emp, wk), g in s.groupby(["employee_id", "week_start"], sort=True):
        first = g.iloc[0]
        hours = float(g["paid_hours"].sum())
        # weighted average rate if the rate changes between shifts
        avg_rate = float(g["base_pay_shift"].sum() / hours) if hours else float(first["hourly_rate"])
        contracted = first["contracted_hours"]
        ot_hours = max(0.0, hours - float(contracted)) if pd.notna(contracted) and contracted else 0.0
        base_pay = float(g["base_pay_shift"].sum())
        ot_premium_pay = ot_hours * avg_rate * max(0.0, overtime_premium - 1.0)
        irregular = first["contract_type"] == "irregular_zero_hours"
        holiday = (base_pay + ot_premium_pay) * rates.holiday_accrual_irregular if (irregular and holiday_for_irregular) else 0.0
        gross = base_pay + ot_premium_pay + holiday
        ni = employer_ni_weekly(gross, first["age_band"], first["contract_type"], first["age"], rates)
        min_rate, min_name = minimum_rate(first["age_band"], first["contract_type"], first["age"], rates)
        ssp = min(rates.ssp_weekly, rates.ssp_awe_share * gross)
        loaded = gross + ni
        rows.append({
            "employee_id": emp,
            "employee_name": first["employee_name"],
            "week_start": wk,
            "sites": ", ".join(sorted(g["site"].unique())),
            "role": first["role"],
            "age_band": first["age_band"],
            "contract_type": first["contract_type"],
            "hourly_rate": round(avg_rate, 4),
            "paid_hours": round(hours, 2),
            "contracted_hours": float(contracted) if pd.notna(contracted) and contracted else float("nan"),
            "overtime_hours": round(ot_hours, 2),
            "base_pay": money(base_pay),
            "overtime_premium_pay": money(ot_premium_pay),
            "holiday_pay": money(holiday),
            "gross_pay": money(gross),
            "employer_ni": ni,
            "ni_threshold": (rates.ni_upper_secondary_threshold_weekly
                             if is_ni_relief(first["age_band"], first["contract_type"], first["age"])
                             else rates.ni_secondary_threshold_weekly),
            "loaded_cost": money(loaded),
            "loaded_hourly_cost": money(loaded / hours) if hours else 0.0,
            "min_rate": min_rate,
            "min_rate_name": min_name,
            "nmw_breach": bool(g["hourly_rate"].min() < min_rate - 1e-9),
            "nmw_shortfall_per_hour": money(max(0.0, min_rate - float(g["hourly_rate"].min()))),
            "ssp_week_exposure": money(ssp),
        })
    return pd.DataFrame(rows)


def site_costs(shifts: pd.DataFrame, emp_week: pd.DataFrame, site_sales: dict[str, float] | None = None) -> pd.DataFrame:
    """Allocate each employee-week's cost to sites by share of paid hours."""
    if shifts.empty or emp_week.empty:
        return pd.DataFrame()
    s = shifts.copy()
    s["week_start"] = s["start"].map(iso_week_start)
    hrs = s.groupby(["employee_id", "week_start", "site"])["paid_hours"].sum().reset_index()
    tot = hrs.groupby(["employee_id", "week_start"])["paid_hours"].transform("sum")
    hrs["share"] = hrs["paid_hours"] / tot
    m = hrs.merge(emp_week[["employee_id", "week_start", "gross_pay", "employer_ni", "holiday_pay", "loaded_cost"]],
                  on=["employee_id", "week_start"])
    for c in ("gross_pay", "employer_ni", "holiday_pay", "loaded_cost"):
        m[c] = m[c] * m["share"]
    out = m.groupby(["week_start", "site"]).agg(
        paid_hours=("paid_hours", "sum"), gross_pay=("gross_pay", "sum"), employer_ni=("employer_ni", "sum"),
        holiday_pay=("holiday_pay", "sum"), loaded_cost=("loaded_cost", "sum"),
        headcount=("employee_id", "nunique")).reset_index()
    for c in ("gross_pay", "employer_ni", "holiday_pay", "loaded_cost"):
        out[c] = out[c].map(money)
    out["paid_hours"] = out["paid_hours"].round(2)
    sales = site_sales or {}
    out["sales"] = out["site"].map(lambda x: sales.get(x))
    out["labour_cost_pct"] = [round(100 * lc / sa, 2) if sa else None for lc, sa in zip(out["loaded_cost"], out["sales"])]
    return out


def totals(emp_week: pd.DataFrame, sites: pd.DataFrame) -> dict:
    if emp_week.empty:
        return {}
    sales = sites["sales"].dropna().sum() if "sales" in sites else 0
    loaded = float(emp_week["loaded_cost"].sum())
    return {
        "paid_hours": round(float(emp_week["paid_hours"].sum()), 2),
        "gross_pay": money(emp_week["gross_pay"].sum()),
        "base_pay": money(emp_week["base_pay"].sum()),
        "overtime_premium_pay": money(emp_week["overtime_premium_pay"].sum()),
        "holiday_pay": money(emp_week["holiday_pay"].sum()),
        "employer_ni": money(emp_week["employer_ni"].sum()),
        "loaded_cost": money(loaded),
        "sales": money(sales) if sales else None,
        "labour_cost_pct": round(100 * loaded / sales, 2) if sales else None,
        "headcount": int(emp_week["employee_id"].nunique()),
        "nmw_breaches": int(emp_week["nmw_breach"].sum()),
        "ssp_exposure_one_week_all_staff": money(emp_week["ssp_week_exposure"].sum()),
    }
