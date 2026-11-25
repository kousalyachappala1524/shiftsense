"""Load real rota exports (CSV / Excel) into ShiftSense's canonical shift table.

- Accepts .csv, .txt and .xlsx up to 5 MB / 5,000 rows.
- Detects encoding (utf-8-sig, utf-8, cp1252, latin-1) and delimiter (, ; tab |).
  Planday payroll CSVs use semicolons.
- Suggests a column mapping from presets (Deputy, Planday, custom) that the user confirms.
- Parses every shift into timezone-aware datetimes; an end time at or before the start
  time means the shift finishes the next day (e.g. 22:00-06:00).
- Never raises a traceback at the user: bad rows come back in an issues table with the
  spreadsheet row number and a plain-English reason.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5_000
MAX_SHIFT_HOURS = 16.0

CANONICAL = {
    # field: (required, description)
    "employee_id": (True, "Employee ID or payroll number (name is used if there is no ID)"),
    "employee_name": (False, "Employee name"),
    "site": (False, "Site / location / department"),
    "role": (False, "Role / position"),
    "age_band": (False, "Age band (16-17, 18-20, 21+) or age"),
    "dob": (False, "Date of birth (used instead of age band)"),
    "contract_type": (False, "fixed, irregular_zero_hours or apprentice_u25"),
    "hourly_rate": (True, "Hourly pay rate"),
    "shift_date": (False, "Shift date (not needed if start includes the date)"),
    "start_time": (True, "Shift start"),
    "end_time": (True, "Shift end"),
    "unpaid_break_mins": (False, "Unpaid break (minutes, or h:mm)"),
    "wtr_48h_opt_out": (False, "Signed the 48-hour opt-out (yes/no)"),
    "contracted_hours": (False, "Contracted hours a week"),
    "gender": (False, "Gender (only used for India night-work reminders)"),
}

_GENERIC = {
    "employee_id": ["employeeid", "empid", "staffid", "payrollid", "payrollnumber", "employeenumber",
                    "employeeno", "salaryidentifier", "exportcode", "employeeexportcode", "id"],
    "employee_name": ["employeename", "name", "staffname", "fullname", "displayname", "employee"],
    "site": ["site", "location", "store", "branch", "department", "workplace", "locationname", "area"],
    "role": ["role", "position", "jobtitle", "employeegroup", "operationalunit", "team", "skill"],
    "age_band": ["ageband", "agegroup", "age"],
    "dob": ["dob", "dateofbirth", "birthdate", "birthday"],
    "contract_type": ["contracttype", "contract", "employmenttype", "employeetype", "paytype"],
    "hourly_rate": ["hourlyrate", "payrate", "rateperhour", "wageperhour", "hourlywage", "baserate", "rate", "wage"],
    "shift_date": ["shiftdate", "date", "workdate", "rosterdate", "day"],
    "start_time": ["starttime", "start", "shiftstart", "from", "clockin", "begin"],
    "end_time": ["endtime", "end", "shiftend", "to", "clockout", "finish"],
    "unpaid_break_mins": ["unpaidbreakmins", "unpaidbreak", "breakmins", "breakminutes", "totalmealbreak",
                          "mealbreak", "breakduration", "break", "breaks"],
    "wtr_48h_opt_out": ["wtr48hoptout", "48houroptout", "wtroptout", "optout"],
    "contracted_hours": ["contractedhours", "contracthours", "weeklycontracthours", "contracthoursperweek"],
    "gender": ["gender", "sex"],
}

# Preset-specific names are tried first; then the generic list. Real exports vary by
# account settings, so the user always confirms the mapping on screen.
PRESETS = {
    "Deputy export": {
        "employee_id": ["employeeexportcode", "exportcode", "payrollid"],
        "employee_name": ["employee", "employeename"],
        "site": ["location", "locationname", "area"],
        "role": ["area", "operationalunit", "position"],
        "unpaid_break_mins": ["totalmealbreak", "mealbreak", "break"],
        "hourly_rate": ["payrate", "rate"],
    },
    "Planday export": {
        "employee_id": ["salaryidentifier", "employeenumber", "employeeid"],
        "employee_name": ["employee", "name"],
        "site": ["department", "location"],
        "role": ["employeegroup", "position"],
        "start_time": ["from", "start"],
        "end_time": ["to", "end"],
        "hourly_rate": ["wage", "hourlywage", "rate"],
    },
    "Custom Excel/CSV": {},
}


class IngestionError(Exception):
    """A problem with the whole file (size, format, missing columns). Message is user-facing."""


@dataclass
class TableInfo:
    filename: str
    file_type: str
    encoding: str | None
    delimiter: str | None
    rows: int


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# ------------------------------------------------------------------ reading

def _decode(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise IngestionError("The file's text encoding couldn't be read. Save it as CSV UTF-8 and try again.")


def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first = text.splitlines()[0] if text else ""
        counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def read_table(data: bytes, filename: str) -> tuple[pd.DataFrame, TableInfo]:
    """Read an uploaded rota file into a DataFrame of strings/raw cells."""
    if len(data) > MAX_BYTES:
        raise IngestionError(f"The file is {len(data) / 1024 / 1024:.1f} MB. The limit is 5 MB: "
                             "split it into smaller date ranges and upload one at a time.")
    if not data:
        raise IngestionError("The file is empty.")
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in ("xlsx", "xlsm"):
        try:
            df = pd.read_excel(io.BytesIO(data), dtype=object)
        except Exception:
            raise IngestionError("The Excel file couldn't be opened. Check it isn't password-protected, "
                                 "or save the rota sheet as CSV.")
        info = TableInfo(filename, "excel", None, None, 0)
    elif ext in ("csv", "txt", ""):
        text, enc = _decode(data)
        delim = sniff_delimiter(text)
        try:
            df = pd.read_csv(io.StringIO(text), sep=delim, dtype=str, keep_default_na=False, skipinitialspace=True)
        except Exception:
            raise IngestionError("The CSV couldn't be read. Check that every row has the same number of columns.")
        info = TableInfo(filename, "csv", enc, delim, 0)
    else:
        raise IngestionError(f"'.{ext}' files aren't supported. Upload a .csv or .xlsx file.")

    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[~df.apply(lambda r: all(str(v).strip() in ("", "nan", "NaT", "None") for v in r), axis=1)]
    if len(df) > MAX_ROWS:
        raise IngestionError(f"The file has {len(df):,} rows. The limit is {MAX_ROWS:,} shifts per upload.")
    if df.empty:
        raise IngestionError("No rows were found in the file.")
    info.rows = len(df)
    return df.reset_index(drop=True), info


# ------------------------------------------------------------------ mapping

def suggest_mapping(columns: list[str], preset: str = "Custom Excel/CSV") -> dict[str, str | None]:
    """Map canonical fields to uploaded column names. Each column is used at most once."""
    norm_cols = {c: _norm(c) for c in columns}
    canonical_names = {f: [_norm(f)] for f in CANONICAL}
    used: set[str] = set()
    mapping: dict[str, str | None] = {f: None for f in CANONICAL}
    candidates = {f: canonical_names[f] + PRESETS.get(preset, {}).get(f, []) + _GENERIC[f] for f in CANONICAL}

    # pass 1: exact matches, in field order; pass 2: header contains a candidate (4+ chars)
    for exact in (True, False):
        for field, cands in candidates.items():
            if mapping[field]:
                continue
            for cand in cands:
                hit = next((c for c, n in norm_cols.items() if c not in used and
                            (n == cand if exact else (len(cand) >= 4 and cand in n))), None)
                if hit:
                    mapping[field] = hit
                    used.add(hit)
                    break
    if not mapping["employee_id"] and mapping["employee_name"]:
        mapping["employee_id"] = mapping["employee_name"]
    return mapping


def missing_required(mapping: dict[str, str | None]) -> list[str]:
    return [f for f, (req, _) in CANONICAL.items() if req and not mapping.get(f)]


# ------------------------------------------------------------------ value parsers

_EXCEL_EPOCH = datetime(1899, 12, 30)


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ("", "nan", "NaT", "None")


def parse_date(v, dayfirst: bool = True) -> date | None:
    if _blank(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:  # Excel serial date
        return (_EXCEL_EPOCH + timedelta(days=float(v))).date()
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}([ T].*)?", s):
        dayfirst = False  # ISO dates are never day-first
    try:
        return pd.to_datetime(s, dayfirst=dayfirst).date()
    except (ValueError, TypeError, OverflowError):
        return None


_TIME_RE = re.compile(r"^(\d{1,2})(?::?(\d{2}))?(?::(\d{2}))?\s*([ap]\.?m\.?)?$", re.I)


def parse_time(v) -> time | datetime | None:
    """Returns a time, or a full datetime if the cell contains a date as well."""
    if _blank(v):
        return None
    if isinstance(v, datetime):
        return v if (v.year > 1900) else v.time()
    if isinstance(v, time):
        return v
    if isinstance(v, (int, float)) and 0 <= float(v) < 1:  # Excel time fraction
        secs = round(float(v) * 86400)
        return time(secs // 3600 % 24, secs % 3600 // 60)
    s = str(v).strip()
    m = _TIME_RE.match(s.replace(" ", "")) if len(s) <= 11 else None
    if m:
        h, mi, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(4)
        if m.group(2) is None and not ampm and len(m.group(1)) > 2:
            return None
        if ampm:
            if h > 12:
                return None
            h = h % 12 + (12 if ampm.lower().startswith("p") else 0)
        if h == 24 and mi == 0:
            h = 0
        if 0 <= h < 24 and 0 <= mi < 60:
            return time(h, mi)
        return None
    if re.fullmatch(r"\d{3,4}", s):  # 0930 / 930
        h, mi = int(s[:-2]), int(s[-2:])
        return time(h, mi) if h < 24 and mi < 60 else None
    try:
        ts = pd.to_datetime(s, dayfirst=not re.match(r"\d{4}-", s))
        return ts.to_pydatetime()
    except (ValueError, TypeError, OverflowError):
        return None


def parse_minutes(v, header: str = "") -> float | None:
    if _blank(v):
        return 0.0
    if isinstance(v, time):
        return v.hour * 60 + v.minute
    s = str(v).strip().lower().replace("mins", "").replace("min", "").strip()
    if ":" in s:
        try:
            h, m = s.split(":")[:2]
            return int(h) * 60 + int(m)
        except ValueError:
            return None
    try:
        x = float(s)
    except ValueError:
        return None
    if x < 0:
        return None
    hours_header = any(k in _norm(header) for k in ("hour", "hrs"))
    return x * 60 if (hours_header or x < 5) else x  # 0.5 -> 30 min; 30 -> 30 min


def parse_money(v) -> float | None:
    if _blank(v):
        return None
    s = re.sub(r"[£₹$€\s]", "", str(v))
    s = re.sub(r"(perhour|/hr|/h|ph)$", "", s.lower())
    if re.fullmatch(r"\d+,\d{1,2}", s):  # decimal comma (e.g. "12,71" from European-locale exports)
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{1,2}", s):  # "1.234,50"
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")  # "1,234.50"
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x >= 0 else None


def parse_bool(v) -> bool:
    return str(v).strip().lower() in ("y", "yes", "true", "1", "signed", "opted out", "opt-out", "optout")


def age_band_from_age(age: float) -> str:
    if age < 18:
        return "16-17"
    if age < 21:
        return "18-20"
    return "21+"


def parse_age_band(v) -> tuple[str | None, float | None]:
    """Returns (band, age if a number was given)."""
    if _blank(v):
        return None, None
    s = str(v).strip().lower()
    if re.fullmatch(r"\d{2}(\.0+)?", s):
        age = float(s)
        return age_band_from_age(age), age
    n = _norm(s)
    if n in ("1617", "u18", "under18", "under18s", "lessthan18", "young", "youngworker") or "under18" in n or s.startswith("<18"):
        return "16-17", None
    if n in ("1820", "18to20", "u21", "under21") or "1820" in n:
        return "18-20", None
    if n in ("21", "21plus", "21andover", "over21", "adult", "nlw") or "21" in n:
        return "21+", None
    return None, None


def parse_contract(v) -> str:
    n = _norm(v)
    if any(k in n for k in ("zero", "casual", "irregular", "bank", "relief", "flex")):
        return "irregular_zero_hours"
    if "apprent" in n:
        return "apprentice_u25"
    return "fixed"


def age_on(dob: date, on: date) -> float:
    years = on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))
    return float(years)


# ------------------------------------------------------------------ canonical shifts

SHIFT_COLUMNS = ["row", "employee_id", "employee_name", "site", "role", "age_band", "age", "contract_type",
                 "hourly_rate", "start", "end", "unpaid_break_mins", "paid_hours", "overnight",
                 "wtr_48h_opt_out", "contracted_hours", "gender"]


def apply_mapping(df: pd.DataFrame, mapping: dict[str, str | None], tz_name: str = "Europe/London",
                  dayfirst: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Convert raw rows to canonical shifts. Returns (shifts, issues, warnings)."""
    miss = missing_required(mapping)
    if miss:
        raise IngestionError("Map these required columns first: " + ", ".join(miss))
    tz = ZoneInfo(tz_name)
    get = {f: (lambda row, c=col: row.get(c) if c else None) for f, col in mapping.items()}
    shifts, issues = [], []
    no_age = 0

    for i, row in enumerate(df.to_dict(orient="records")):
        rownum = i + 2  # spreadsheet row (row 1 is the header)

        def bad(reason, value=None):
            issues.append({"row": rownum, "employee": str(get["employee_id"](row) or "").strip(),
                           "problem": reason, "value": "" if value is None else str(value)})

        emp = get["employee_id"](row)
        if _blank(emp):
            bad("Missing employee ID or name")
            continue
        emp = str(emp).strip()
        rate = parse_money(get["hourly_rate"](row))
        if rate is None:
            bad("Hourly rate is missing or not a number", get["hourly_rate"](row))
            continue
        st_raw, en_raw = get["start_time"](row), get["end_time"](row)
        st, en = parse_time(st_raw), parse_time(en_raw)
        if st is None:
            bad("Start time couldn't be read", st_raw)
            continue
        if en is None:
            bad("End time couldn't be read", en_raw)
            continue
        d = parse_date(get["shift_date"](row), dayfirst) if mapping.get("shift_date") else None
        if d is None and isinstance(st, datetime):
            d = st.date()
        if d is None:
            bad("Shift date is missing or couldn't be read", get["shift_date"](row))
            continue
        start = datetime.combine(d, st.time() if isinstance(st, datetime) else st, tzinfo=tz)
        end = (en.replace(tzinfo=tz) if isinstance(en, datetime)
               else datetime.combine(d, en, tzinfo=tz))
        overnight = False
        if end <= start and not isinstance(en, datetime):  # time-only end at/before start: finishes next day
            end = datetime.combine(start.date() + timedelta(days=1), end.time(), tzinfo=tz)
            overnight = True
        length_h = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600
        if length_h <= 0:
            bad("End time is before the start time", f"{st_raw} - {en_raw}")
            continue
        if length_h > MAX_SHIFT_HOURS:
            bad(f"Shift is {length_h:.1f} hours long; check the start and end", f"{st_raw} - {en_raw}")
            continue
        brk = parse_minutes(get["unpaid_break_mins"](row), mapping.get("unpaid_break_mins") or "")
        if brk is None:
            bad("Break length couldn't be read", get["unpaid_break_mins"](row))
            continue
        if brk >= length_h * 60:
            bad("Break is as long as the shift", get["unpaid_break_mins"](row))
            continue

        band, age = parse_age_band(get["age_band"](row))
        dob = parse_date(get["dob"](row), dayfirst) if mapping.get("dob") else None
        if dob:
            age = age_on(dob, d)
            band = age_band_from_age(age)
        if band is None:
            band = "21+"
            no_age += 1
        contracted = parse_money(get["contracted_hours"](row)) if mapping.get("contracted_hours") else None
        shifts.append({
            "row": rownum,
            "employee_id": emp,
            "employee_name": str(get["employee_name"](row) or emp).strip() if mapping.get("employee_name") else emp,
            "site": str(get["site"](row) or "").strip() or "Main site",
            "role": str(get["role"](row) or "").strip() or "Team member",
            "age_band": band,
            "age": age,
            "contract_type": parse_contract(get["contract_type"](row)) if mapping.get("contract_type") else "fixed",
            "hourly_rate": rate,
            "start": pd.Timestamp(start),
            "end": pd.Timestamp(end),
            "unpaid_break_mins": float(brk),
            "paid_hours": round(length_h - brk / 60, 4),
            "overnight": overnight,
            "wtr_48h_opt_out": parse_bool(get["wtr_48h_opt_out"](row)) if mapping.get("wtr_48h_opt_out") else False,
            "contracted_hours": contracted,
            "gender": str(get["gender"](row) or "").strip().upper()[:1] if mapping.get("gender") else "",
        })

    warnings = []
    if no_age:
        warnings.append(f"{no_age} shift(s) had no age band or date of birth, so they were treated as 21+. "
                        "Under-18 and 18-20 checks need an age band or date of birth.")
    out = pd.DataFrame(shifts, columns=SHIFT_COLUMNS)
    if not out.empty:
        out = out.sort_values(["employee_id", "start"]).reset_index(drop=True)
    issue_df = pd.DataFrame(issues, columns=["row", "employee", "problem", "value"])
    return out, issue_df, warnings


def load_rota(data: bytes, filename: str, preset: str = "Custom Excel/CSV",
              tz_name: str = "Europe/London") -> tuple[pd.DataFrame, pd.DataFrame, list[str], TableInfo, dict]:
    """One-call helper: read, auto-map and parse. Used for the built-in demo and in tests."""
    df, info = read_table(data, filename)
    mapping = suggest_mapping(list(df.columns), preset)
    shifts, issues, warnings = apply_mapping(df, mapping, tz_name)
    return shifts, issues, warnings, info, mapping
