import pytest

from config.uk_rates import UK_2026_27, UK_2027_28_PREVIEW
from engine import uk_cost
from tests.conftest import make_shifts


# ---------- Employer NI boundaries

def test_ni_zero_below_secondary_threshold():
    assert uk_cost.employer_ni_weekly(95.99) == 0.0
    assert uk_cost.employer_ni_weekly(96.00) == 0.0


def test_ni_starts_just_above_threshold():
    assert uk_cost.employer_ni_weekly(96.01, rounded=False) == pytest.approx(0.0015)
    assert uk_cost.employer_ni_weekly(96.01) == 0.00  # rounds to nothing at the penny
    assert uk_cost.employer_ni_weekly(100.00) == 0.60


def test_ni_spec_example_20h_at_nlw():
    gross = 20 * 12.71
    assert gross == pytest.approx(254.20)
    assert uk_cost.employer_ni_weekly(gross) == 23.73


def test_under_21_pays_no_ni_up_to_upper_secondary_threshold():
    assert uk_cost.employer_ni_weekly(500, "18-20") == 0.0
    assert uk_cost.employer_ni_weekly(967, "16-17") == 0.0
    assert uk_cost.employer_ni_weekly(1000, "18-20") == pytest.approx(4.95)


def test_apprentice_relief_only_under_25():
    assert uk_cost.employer_ni_weekly(400, "21+", "apprentice_u25", age=22) == 0.0
    assert uk_cost.employer_ni_weekly(400, "21+", "apprentice_u25", age=26) == pytest.approx(45.60)


# ---------- minimum wage

@pytest.mark.parametrize("band,contract,expected", [
    ("21+", "fixed", 12.71), ("18-20", "fixed", 10.85), ("16-17", "fixed", 8.00), ("21+", "apprentice_u25", 8.00)])
def test_minimum_rates_2026_27(band, contract, expected):
    assert uk_cost.minimum_rate(band, contract)[0] == expected


def test_2027_preview_rates():
    assert (UK_2027_28_PREVIEW.nlw_21_plus, UK_2027_28_PREVIEW.nmw_18_20, UK_2027_28_PREVIEW.nmw_16_17) == (13.21, 11.27, 8.31)
    assert UK_2026_27.version == "2026-27" and UK_2026_27.employer_ni_rate == 0.15


def test_nmw_breach_flagged_one_penny_below():
    s = make_shifts([{"id": "A", "rate": 12.70, "date": "2026-11-30", "start": "09:00", "end": "17:00", "brk": 30},
                     {"id": "B", "rate": 12.71, "date": "2026-11-30", "start": "09:00", "end": "17:00", "brk": 30}])
    c = uk_cost.weekly_costs(s).set_index("employee_id")
    assert bool(c.loc["A", "nmw_breach"]) and not bool(c.loc["B", "nmw_breach"])


def test_dob_turning_21_moves_to_nlw(demo_shifts):
    jordan = demo_shifts[demo_shifts["employee_name"] == "Jordan Lee"].iloc[0]
    assert jordan["age"] == 21 and jordan["age_band"] == "21+"
    c = uk_cost.weekly_costs(demo_shifts)
    row = c[c["employee_name"] == "Jordan Lee"].iloc[0]
    assert row["nmw_breach"] and row["min_rate"] == 12.71


# ---------- holiday accrual, overtime premium, allocation

def test_holiday_accrual_only_for_irregular_hours_and_separate():
    s = make_shifts([{"id": "Z", "contract": "Zero hours", "rate": 10, "date": "2026-11-30", "start": "09:00", "end": "19:00"},
                     {"id": "F", "contract": "Permanent", "rate": 10, "date": "2026-11-30", "start": "09:00", "end": "19:00"}])
    c = uk_cost.weekly_costs(s).set_index("employee_id")
    assert c.loc["Z", "holiday_pay"] == 12.07 and c.loc["F", "holiday_pay"] == 0.0
    assert c.loc["Z", "base_pay"] == 100.0  # holiday is never folded into the base
    assert c.loc["Z", "employer_ni"] == uk_cost.employer_ni_weekly(112.07)


def test_overtime_premium_applies_above_contract_only():
    s = make_shifts([{"id": "O", "rate": 10, "contracted": 8, "date": "2026-11-30", "start": "08:00", "end": "18:00"}])
    c = uk_cost.weekly_costs(s, overtime_premium=1.5).iloc[0]
    assert c["overtime_hours"] == 2.0
    assert c["base_pay"] == 100.0 and c["overtime_premium_pay"] == 10.0


def test_site_allocation_adds_up(demo_shifts):
    emp = uk_cost.weekly_costs(demo_shifts, overtime_premium=1.5)
    sites = uk_cost.site_costs(demo_shifts, emp)
    assert sites["loaded_cost"].sum() == pytest.approx(emp["loaded_cost"].sum(), abs=0.05)
    assert sites["paid_hours"].sum() == pytest.approx(emp["paid_hours"].sum(), abs=0.01)


def test_money_rounds_half_up():
    assert uk_cost.money(0.125) == 0.13 and uk_cost.money(2.675) == 2.68
