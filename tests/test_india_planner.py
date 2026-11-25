"""The original India planner (forecast + rota) and the Labour Codes checks."""

import pandas as pd
import pytest

from config.india_rates import PRESETS, IndiaPreset
from engine import claude_brief as cb
from engine import india_rules
from engine.demo_data import demo_bundle
from engine.india_pipeline import plan_week, store_forecast, stores
from engine.rota_builder import Rules


@pytest.fixture(scope="module")
def hyd():
    b = demo_bundle()
    s = stores(b["demand"]).set_index("store_id").loc["HYD01"]
    s["store_id"] = "HYD01"
    f = store_forecast(b["demand"], b["events"], "HYD01")
    return b, s, f


def test_demo_data_is_deterministic_and_fictional():
    a, b = demo_bundle(), demo_bundle()
    pd.testing.assert_frame_equal(a["demand"], b["demand"])
    assert set(a["staff"]["gender"]) == {"F", "M"} and len(a["staff"]) == 51


def test_forecast_backtest_accuracy(hyd):
    _, _, f = hyd
    assert f["backtest"]["mape_daily_footfall"] < 12
    assert "Diwali festive week" in f["model"]["uplifts"]
    assert f["start"].day_of_week == 0  # planning weeks start on Monday


def test_rota_respects_rules(hyd):
    b, s, f = hyd
    rules = Rules()
    plan = plan_week(f["forecast"], b["staff"], s, f["start"], rules)
    staff, shifts = plan["rota"]["staff"], plan["rota"]["shifts"]
    assert (staff["scheduled_hours"] <= rules.weekly_hours_cap).all()
    assert (staff["days_worked"] <= rules.max_days_per_week).all()
    assert (shifts["hours"] <= rules.max_shift_hours).all() and (shifts["hours"] >= rules.min_shift_hours).all()
    for _, g in shifts.groupby("staff_id"):  # rest between consecutive days
        g = g.sort_values("day_idx")
        for (_, a), (_, c) in zip(g.iloc[:-1].iterrows(), g.iloc[1:].iterrows()):
            if c["day_idx"] == a["day_idx"] + 1:
                assert (24 - a["end"]) + c["start"] >= rules.min_rest_hours
    assert plan["kpis"]["coverage_pct"] > 95
    req = plan["rota"]["required"]
    assert req.min() >= rules.min_staff


def test_labour_codes_checks(hyd):
    b, s, f = hyd
    plan = plan_week(f["forecast"], b["staff"], s, f["start"], Rules())
    sh = india_rules.rota_to_shifts(plan["rota"]["shifts"], b["staff"], f["start"])
    assert str(sh["start_ts"].iloc[0].tz) == "Asia/Kolkata"
    findings, people, tot = india_rules.check(sh, PRESETS["Telangana"])
    assert tot["women_night_shift_reminders"] > 0  # store closes at 22:00
    women_late = sh[(sh["gender"] == "F") & (sh["end"] > 19)]
    assert tot["women_night_shift_reminders"] == len(women_late)
    strict = IndiaPreset("Test", 6, 30, 10, "")
    f2, p2, t2 = india_rules.check(sh, strict)
    assert t2["warnings"] > 0 and t2["breaches"] > 0 and t2["overtime_pay_inr"] > 0


def test_india_payload_is_compact(hyd):
    b, s, f = hyd
    plan = plan_week(f["forecast"], b["staff"], s, f["start"], Rules())
    p = cb.build_india_payload("Hyderabad", "Week 1", plan, f, Rules().as_dict())
    assert cb.approx_tokens(p) < cb.TOKEN_BUDGET
    assert "#### Headline" in cb.india_offline_report(p)
