import pytest

from engine import optimizer, uk_cost, wtr_rules


@pytest.fixture(scope="module")
def wins(demo_shifts):
    return optimizer.quick_wins(demo_shifts, overtime_premium=1.5)


def test_demo_savings_in_expected_range(wins):
    assert 85 <= wins["weekly_saving"] <= 120
    assert wins["monthly_saving"] == uk_cost.money(wins["weekly_saving"] * 52 / 12)
    assert len(wins["cost_actions"]) == 3


def test_only_overtime_hours_move_and_contracts_hold(demo_shifts, wins):
    before = uk_cost.weekly_costs(demo_shifts).set_index("employee_name")
    after = uk_cost.weekly_costs(wins["optimised_shifts"]).set_index("employee_name")
    for a in wins["cost_actions"]:
        donor = a["from"].split(" (")[0]
        assert after.loc[donor, "paid_hours"] >= before.loc[donor, "contracted_hours"]  # never below contract
        rec = a["to"]
        ch = after.loc[rec, "contracted_hours"]
        if ch == ch and ch:  # contracted colleague stays within contract
            assert after.loc[rec, "paid_hours"] <= ch


def test_moves_create_no_new_working_time_issues(demo_shifts, wins):
    def issues(df, who):
        f = wtr_rules.check_shifts(df[df["employee_name"] == who])
        return 0 if f.empty else int(f["severity"].isin(["breach", "warning"]).sum())
    for a in wins["cost_actions"]:
        assert issues(wins["optimised_shifts"], a["to"]) <= issues(demo_shifts, a["to"])


def test_total_cost_really_falls_by_the_reported_saving(demo_shifts, wins):
    before = uk_cost.weekly_costs(demo_shifts, overtime_premium=1.5)["loaded_cost"].sum()
    after = uk_cost.weekly_costs(wins["optimised_shifts"], overtime_premium=1.5)["loaded_cost"].sum()
    assert before - after == pytest.approx(wins["weekly_saving"], abs=0.05)


def test_age_is_never_used_to_choose_recipients(demo_shifts):
    """Making every candidate 21+ must not change who is offered the hours."""
    base = optimizer.quick_wins(demo_shifts, overtime_premium=1.5)
    same_age = demo_shifts.copy()
    same_age["age_band"] = "21+"
    same_age["age"] = 30.0
    same_age.loc[same_age["contract_type"] == "apprentice_u25", "contract_type"] = "fixed"
    alt = optimizer.quick_wins(same_age, overtime_premium=1.5)
    assert [a["to"] for a in base["cost_actions"]] == [a["to"] for a in alt["cost_actions"]]


def test_every_breach_gets_a_fix(demo_shifts, wins):
    who = sorted(f["who"] for f in wins["compliance_fixes"])
    assert who == ["Ellie Robinson", "Jordan Lee", "Maisie Clarke", "Mark Davies", "Rahim Uddin"]
    nmw = next(f for f in wins["compliance_fixes"] if f["type"] == "Fix minimum wage")
    assert "£12.71" in nmw["action"]


def test_premium_of_one_means_no_overtime_premium_savings(demo_shifts):
    w = optimizer.quick_wins(demo_shifts, overtime_premium=1.0)
    assert w["weekly_saving"] < 40  # only NI threshold and holiday effects remain


def test_role_compatibility():
    assert optimizer.compatible("Sales Advisor", "Weekend Advisor")
    assert not optimizer.compatible("Store Manager", "Sales Advisor")
    assert not optimizer.compatible("Assistant Manager", "Sales Advisor")
