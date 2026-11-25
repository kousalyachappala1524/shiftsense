"""Compliance audit pack: per-shift flags and costs, the brief, and tamper-evident metadata."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import pandas as pd

from config.uk_rates import WTR, UKRates

from . import wtr_rules
from .uk_cost import iso_week_start, money

APP_VERSION = "ShiftSense 2.0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_rows(shifts: pd.DataFrame, emp_week: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """One row per shift: times, pay, the shift's share of loaded cost, and every rule it touches."""
    s = shifts.copy()
    s["week_start"] = s["start"].map(iso_week_start)
    m = s.merge(emp_week[["employee_id", "week_start", "paid_hours", "loaded_cost"]].rename(
        columns={"paid_hours": "week_paid_hours", "loaded_cost": "week_loaded_cost"}),
        on=["employee_id", "week_start"], how="left")
    m["shift_base_pay"] = (m["paid_hours"] * m["hourly_rate"]).map(money)
    m["shift_loaded_cost"] = (m["week_loaded_cost"] * m["paid_hours"] / m["week_paid_hours"]).map(money)
    m["compliance_flags"] = wtr_rules.shift_flags(m, flags).values
    out = m[["row", "employee_id", "employee_name", "site", "role", "age_band", "contract_type", "start", "end",
             "overnight", "unpaid_break_mins", "paid_hours", "hourly_rate", "shift_base_pay", "shift_loaded_cost",
             "compliance_flags"]].copy()
    out["start"] = out["start"].map(lambda t: t.isoformat())
    out["end"] = out["end"].map(lambda t: t.isoformat())
    return out.sort_values("row")


def build_pack(shifts: pd.DataFrame, emp_week: pd.DataFrame, flags: pd.DataFrame, brief_md: str,
               rates: UKRates, input_bytes: bytes, input_name: str, brief_source: str,
               overtime_premium: float) -> tuple[bytes, dict]:
    rows = audit_rows(shifts, emp_week, flags)
    rows_csv = rows.to_csv(index=False).encode("utf-8")
    meta = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app_version": APP_VERSION,
        "uk_rate_table_version": rates.version,
        "uk_rates": rates.as_dict(),
        "working_time_limits": WTR.as_dict(),
        "overtime_premium_policy": overtime_premium,
        "input_file": input_name,
        "input_sha256": sha256_bytes(input_bytes),
        "rota_audit_rows_sha256": sha256_bytes(rows_csv),
        "shifts": int(len(shifts)),
        "employees": int(shifts["employee_id"].nunique()),
        "findings": wtr_rules.summary(flags),
        "nmw_breaches": int(emp_week["nmw_breach"].sum()),
        "brief_source": brief_source,
        "retention_note": (f"Keep holiday records for {WTR.holiday_record_retention_years} years (from 6 April 2026) and "
                           f"working-time records for at least {WTR.hours_record_retention_years}. This pack is "
                           "supporting evidence, not a substitute for payroll records."),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("rota_audit_rows.csv", rows_csv)
        z.writestr("weekly_management_brief.md", brief_md)
        z.writestr("audit_metadata.json", json.dumps(meta, indent=2, default=str))
    return buf.getvalue(), meta
