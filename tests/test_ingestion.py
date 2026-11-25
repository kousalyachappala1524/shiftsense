import io

import pandas as pd
import pytest

from engine import ingestion

PLANDAY = ("Employee number;Employee;Department;Employee group;Date of birth;Contract;Date;From;To;Break;Wage\n"
           "P1;Ana;Shoreditch;Sales Advisor;01/02/2000;Permanent;30/11/2026;09:00;17:30;30;12,71\n"
           "P2;Bo;Shoreditch;Sales Advisor;15/06/2009;Zero hours;01/12/2026;22:00;06:00;0:30;8.40\n")


def test_semicolon_planday_csv_with_uk_dates():
    shifts, issues, warnings, info, mapping = ingestion.load_rota(PLANDAY.encode("utf-8"), "export.csv", "Planday export")
    assert info.delimiter == ";" and issues.empty
    assert mapping["employee_id"] == "Employee number" and mapping["start_time"] == "From" and mapping["end_time"] == "To"
    a, b = shifts.set_index("employee_id").loc["P1"], shifts.set_index("employee_id").loc["P2"]
    assert a["start"].month == 11 and a["start"].day == 30  # 30/11 read day-first
    assert a["hourly_rate"] == 12.71  # "12,71" is a decimal comma, not £1,271
    assert b["overnight"] and b["end"].day == 2 and b["paid_hours"] == 7.5
    assert b["age_band"] == "16-17" and b["contract_type"] == "irregular_zero_hours"


def test_tab_delimited_cp1252_with_pound_signs():
    text = "Employee\tRate\tDate\tStart\tEnd\nSam\t£12.71\t2026-12-01\t9am\t5:30 PM\n"
    shifts, issues, *_ = ingestion.load_rota(text.encode("cp1252"), "rota.txt")
    assert issues.empty
    s = shifts.iloc[0]
    assert s["hourly_rate"] == 12.71 and s["start"].hour == 9 and s["end"].hour == 17 and s["end"].minute == 30


def test_malformed_rows_are_reported_not_raised():
    text = ("employee_id,hourly_rate,shift_date,start_time,end_time,unpaid_break_mins\n"
            "A,12.71,2026-12-01,09:00,17:00,30\n"
            "B,abc,2026-12-01,09:00,17:00,30\n"
            "C,12.71,,09:00,17:00,30\n"
            "D,12.71,2026-12-01,25:99,17:00,30\n"
            "E,12.71,2026-12-01,09:00,09:00,30\n"
            ",12.71,2026-12-01,09:00,17:00,0\n")
    shifts, issues, *_ = ingestion.load_rota(text.encode(), "bad.csv")
    assert shifts["employee_id"].tolist() == ["A"]
    probs = dict(zip(issues["row"], issues["problem"]))
    assert "not a number" in probs[3] and "date" in probs[4].lower() and "Start time" in probs[5]
    assert "16" in probs[6] or "long" in probs[6]  # 09:00-09:00 = 24h: rejected as too long
    assert "employee" in probs[7].lower()


def test_negative_duration_with_explicit_dates():
    text = "employee_id,hourly_rate,start_time,end_time\nA,12.71,2026-12-01 17:00,2026-12-01 09:00\n"
    shifts, issues, *_ = ingestion.load_rota(text.encode(), "x.csv")
    assert shifts.empty and "before the start" in issues.iloc[0]["problem"]


def test_xlsx_upload_with_excel_times(tmp_path):
    df = pd.DataFrame({"Employee": ["Kim"], "Rate": [13.5], "Date": [pd.Timestamp("2026-12-02")],
                       "Start": [pd.Timestamp("2026-12-02 08:15").time()], "End": [pd.Timestamp("2026-12-02 16:45").time()],
                       "Break": [0.5]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    shifts, issues, *_ = ingestion.load_rota(buf.getvalue(), "rota.xlsx")
    s = shifts.iloc[0]
    assert issues.empty and s["start"].minute == 15 and s["unpaid_break_mins"] == 30 and s["paid_hours"] == 8.0


def test_limits_reject_big_files():
    with pytest.raises(ingestion.IngestionError, match="5 MB"):
        ingestion.read_table(b"x" * (5 * 1024 * 1024 + 1), "big.csv")
    rows = "employee_id,hourly_rate,shift_date,start_time,end_time\n" + "A,12.71,2026-12-01,09:00,17:00\n" * 5001
    with pytest.raises(ingestion.IngestionError, match="5,000"):
        ingestion.read_table(rows.encode(), "many.csv")


def test_missing_required_columns_raise_clear_error():
    df = pd.DataFrame({"Name": ["A"], "Date": ["2026-12-01"]})
    with pytest.raises(ingestion.IngestionError, match="hourly_rate"):
        ingestion.apply_mapping(df, ingestion.suggest_mapping(list(df.columns)))


@pytest.mark.parametrize("raw,value", [("12,71", 12.71), ("£12.71", 12.71), ("1,234.50", 1234.5), ("1.234,50", 1234.5),
                                       ("12.71/hr", 12.71), ("abc", None), ("-3", None)])
def test_money_formats(raw, value):
    assert ingestion.parse_money(raw) == value


@pytest.mark.parametrize("raw,minutes",[("0:30", 30), ("0.5", 30), ("30", 30), ("45 mins", 45), ("", 0)])
def test_break_formats(raw, minutes):
    assert ingestion.parse_minutes(raw) == minutes


@pytest.mark.parametrize("raw,hm", [("0930", (9, 30)), ("930", (9, 30)), ("9:30 PM", (21, 30)), ("21:00:00", (21, 0)),
                                    ("12am", (0, 0)), ("24:00", (0, 0))])
def test_time_formats(raw, hm):
    t = ingestion.parse_time(raw)
    assert (t.hour, t.minute) == hm


def test_deputy_preset_prefers_export_code_and_location():
    cols = ["Employee", "Employee Export Code", "Location", "Area", "Date", "Start", "End", "Total Meal Break", "Pay Rate"]
    m = ingestion.suggest_mapping(cols, "Deputy export")
    assert m["employee_id"] == "Employee Export Code" and m["site"] == "Location" and m["role"] == "Area"
    assert m["unpaid_break_mins"] == "Total Meal Break" and m["hourly_rate"] == "Pay Rate"
