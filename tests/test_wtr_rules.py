import pytest

from engine import wtr_rules
from tests.conftest import make_shifts


def rules(flags, severity=None):
    f = flags if severity is None else flags[flags["severity"] == severity]
    return sorted(f["rule"].tolist())


# ---------- overnight and clock changes

def test_midnight_shift_is_8_hours_and_a_night_shift():
    s = make_shifts([{"date": "2026-12-03", "start": "22:00", "end": "06:00", "brk": 30}])
    assert s.iloc[0]["overnight"] and s.iloc[0]["paid_hours"] == 7.5
    assert s.iloc[0]["end"].day == 4
    assert rules(wtr_rules.check_shifts(s)) == ["Night shift"]


def test_clock_change_night_counts_real_elapsed_hours():
    # UK clocks go back at 02:00 on Sunday 25 Oct 2026: 22:00-06:00 is 9 real hours
    s = make_shifts([{"date": "2026-10-24", "start": "22:00", "end": "06:00", "brk": 0}])
    assert s.iloc[0]["paid_hours"] == pytest.approx(9.0)


# ---------- daily rest (clopening)

def test_10_and_a_half_hour_gap_is_a_clopening_breach():
    s = make_shifts([{"date": "2026-12-04", "start": "12:30", "end": "21:00", "brk": 30},
                     {"date": "2026-12-05", "start": "07:30", "end": "16:00", "brk": 30}])
    f = wtr_rules.check_shifts(s)
    assert rules(f, "breach") == ["Daily rest (clopening)"]
    assert "10.5h" in f.iloc[0]["detail"]


def test_exactly_11_hours_rest_is_fine():
    s = make_shifts([{"date": "2026-12-04", "start": "12:00", "end": "20:00", "brk": 30},
                     {"date": "2026-12-05", "start": "07:00", "end": "15:00", "brk": 30}])
    assert wtr_rules.check_shifts(s).empty


def test_clopening_across_sites_is_caught():
    s = make_shifts([{"site": "Covent Garden", "date": "2026-12-01", "start": "13:30", "end": "22:00", "brk": 30},
                     {"site": "Shoreditch", "date": "2026-12-02", "start": "07:00", "end": "15:30", "brk": 30}])
    f = wtr_rules.check_shifts(s)
    assert rules(f, "breach") == ["Daily rest (clopening)"]
    assert "Covent Garden → Shoreditch" in f.iloc[0]["detail"]


def test_under_18_needs_12_hours_rest():
    s = make_shifts([{"age": "16-17", "date": "2026-12-05", "start": "10:00", "end": "14:00"},
                     {"age": "16-17", "date": "2026-12-06", "start": "01:30", "end": "04:00"}])
    assert "Daily rest (clopening)" in rules(wtr_rules.check_shifts(s), "breach")


def test_overlapping_shifts_are_a_breach():
    s = make_shifts([{"site": "A", "date": "2026-12-01", "start": "09:00", "end": "13:00"},
                     {"site": "B", "date": "2026-12-01", "start": "12:00", "end": "16:00"}])
    assert rules(wtr_rules.check_shifts(s), "breach") == ["Overlapping shifts"]


# ---------- breaks

@pytest.mark.parametrize("start,end,brk,breach", [
    ("09:00", "15:00", 0, False),   # exactly 6h worked: no entitlement
    ("09:00", "16:00", 15, True),   # 6.75h worked, 15 min
    ("09:00", "16:00", 20, False),  # 20 min is enough
])
def test_adult_break_rule(start, end, brk, breach):
    f = wtr_rules.check_shifts(make_shifts([{"date": "2026-12-01", "start": start, "end": end, "brk": brk}]))
    assert ("Rest break" in rules(f, "breach")) is breach


def test_under_18_five_hours_with_20_minute_break_is_a_breach():
    f = wtr_rules.check_shifts(make_shifts([{"age": "16-17", "date": "2026-12-05", "start": "10:00", "end": "15:00", "brk": 20}]))
    assert rules(f, "breach") == ["Rest break"]
    ok = wtr_rules.check_shifts(make_shifts([{"age": "16-17", "date": "2026-12-05", "start": "10:00", "end": "15:00", "brk": 30}]))
    assert ok.empty


# ---------- weekly limits

def seven_days(**kw):
    return [{"date": f"2026-11-{30 if d == 0 else ''}" if d == 0 else f"2026-12-{d:02d}", "start": "09:00",
             "end": "17:00", "brk": 30, **kw} for d in range(0, 7)]


def test_adult_without_24h_weekly_rest_gets_a_warning():
    f = wtr_rules.check_shifts(make_shifts(seven_days()))
    assert "Weekly rest" in rules(f, "warning")


def test_young_worker_weekly_rest_and_hours_are_breaches():
    f = wtr_rules.check_shifts(make_shifts(seven_days(age="16-17")))
    assert {"Weekly rest", "Young worker weekly hours"} <= set(rules(f, "breach"))


def test_48_hours_warning_respects_opt_out():
    days = [{"date": f"2026-12-0{d}", "start": "08:00", "end": "18:30", "brk": 30} for d in range(1, 6)]
    assert "48-hour week" in rules(wtr_rules.check_shifts(make_shifts(days)), "warning")
    opted = [dict(d, opt="Yes") for d in days]
    assert "48-hour week" not in rules(wtr_rules.check_shifts(make_shifts(opted)))


def test_young_worker_over_8_hours_a_day():
    f = wtr_rules.check_shifts(make_shifts([{"age": "16-17", "date": "2026-12-05", "start": "08:00", "end": "17:30", "brk": 30}]))
    assert "Young worker daily hours" in rules(f, "breach")


# ---------- the demo rota has exactly the intended traps

def test_demo_rota_traps(demo_shifts):
    f = wtr_rules.check_shifts(demo_shifts)
    breaches = f[f["severity"] == "breach"]
    assert len(breaches) == 4
    assert sorted(breaches["employee_name"]) == ["Ellie Robinson", "Maisie Clarke", "Mark Davies", "Rahim Uddin"]
    assert breaches[breaches["age_band"] == "16-17"]["rule"].tolist() == ["Rest break"]
    assert rules(f, "info") == ["Night shift"]


def test_audit_flags_map_back_to_file_rows(demo_shifts):
    f = wtr_rules.check_shifts(demo_shifts)
    flagged = wtr_rules.shift_flags(demo_shifts, f)
    assert (flagged != "").sum() == 7  # 2 + 2 clopening rows, 2 break rows, 1 night shift
