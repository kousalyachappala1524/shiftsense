"""Synthetic demo data for a fictional 3-store fashion retailer in India.

Everything here is invented and seeded, so every run gives the same numbers.
No real company, store or employee data is used.

Schemas (also used for CSV upload):

demand.csv  store_id, store_name, timestamp (YYYY-MM-DD HH:00), footfall, transactions, sales_inr
staff.csv   staff_id, store_id, name, role, contract_hours, max_hours, hourly_cost_inr,
            key_holder (true/false), skills ("a|b"), avail_mon .. avail_sun ("10-22" or "off")
events.csv  event, start_date, end_date            (optional)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BRAND = "Loom & Lane"  # fictional brand
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

HISTORY_START = pd.Timestamp("2025-09-29")  # Monday
HISTORY_END = pd.Timestamp("2026-11-15")  # Sunday (last day of actuals)

STORES = pd.DataFrame(
    [
        # store_id, store_name, city, open, close, base footfall/hr, conversion, avg basket (INR)
        ("HYD01", "Hyderabad - Jubilee Hills", "Hyderabad", 10, 22, 47, 0.16, 1900),
        ("BLR01", "Bengaluru - Indiranagar", "Bengaluru", 10, 22, 54, 0.15, 2000),
        ("MUM01", "Mumbai - Bandra West", "Mumbai", 11, 23, 60, 0.16, 2100),
    ],
    columns=["store_id", "store_name", "city", "open_hour", "close_hour", "base_ff", "conv", "atv"],
)

# Retail calendar: (event, start, end, footfall factor, conversion factor, basket factor)
EVENTS = [
    ("Diwali festive week", "2025-10-14", "2025-10-21", 1.45, 1.05, 1.25),
    ("Black Friday weekend", "2025-11-28", "2025-11-30", 1.30, 1.15, 0.92),
    ("Winter end-of-season sale", "2025-12-19", "2026-01-11", 1.30, 1.20, 0.80),
    ("Republic Day sale", "2026-01-23", "2026-01-26", 1.20, 1.10, 0.90),
    ("Summer end-of-season sale", "2026-06-19", "2026-07-12", 1.30, 1.20, 0.80),
    ("Independence Day sale", "2026-08-14", "2026-08-16", 1.20, 1.10, 0.90),
    ("Diwali festive week", "2026-11-02", "2026-11-08", 1.45, 1.05, 1.25),
    ("Black Friday weekend", "2026-11-27", "2026-11-29", 1.30, 1.15, 0.92),
    ("Winter end-of-season sale", "2026-12-18", "2027-01-10", 1.30, 1.20, 0.80),
]
# Salary-day boost on the 1st-3rd of every month (listed after the big events,
# so a sale that overlaps it takes priority in both the generator and the forecast)
EVENTS += [
    ("Salary-day boost", f"{m:%Y-%m}-01", f"{m:%Y-%m}-03", 1.10, 1.05, 1.0)
    for m in pd.date_range("2025-10-01", "2027-01-01", freq="MS")
]

DOW_FACTOR = np.array([0.82, 0.80, 0.85, 0.90, 1.05, 1.38, 1.45])

# Relative hourly shape from opening time (12 trading hours), weekday vs weekend
WEEKDAY_SHAPE = np.array([0.35, 0.50, 0.75, 0.90, 0.85, 0.80, 0.85, 1.00, 1.25, 1.40, 1.30, 0.90])
WEEKEND_SHAPE = np.array([0.45, 0.70, 0.95, 1.15, 1.20, 1.15, 1.10, 1.15, 1.30, 1.35, 1.15, 0.80])


def events_frame() -> pd.DataFrame:
    df = pd.DataFrame(EVENTS, columns=["event", "start_date", "end_date", "ff", "conv", "atv"])
    df["start_date"] = pd.to_datetime(df["start_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    return df


def _shape(n_hours: int, weekend: bool) -> np.ndarray:
    base = WEEKEND_SHAPE if weekend else WEEKDAY_SHAPE
    if n_hours == len(base):
        shape = base
    else:  # stretch the curve to the store's trading hours
        shape = np.interp(np.linspace(0, len(base) - 1, n_hours), np.arange(len(base)), base)
    return shape / shape.mean()


def generate_demand(seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ev = events_frame()
    dates = pd.date_range(HISTORY_START, HISTORY_END, freq="D")
    rows = []
    for _, s in STORES.iterrows():
        hours = np.arange(s.open_hour, s.close_hour)
        for d in dates:
            dow = d.day_of_week
            trend = 1 + 0.08 * (d - HISTORY_START).days / 365
            hit = ev[(ev.start_date <= d) & (ev.end_date >= d)]
            f_ff, f_conv, f_atv = (1.0, 1.0, 1.0) if hit.empty else hit.iloc[0][["ff", "conv", "atv"]]
            day_noise = rng.lognormal(0, 0.05)
            mean_ff = (
                s.base_ff * DOW_FACTOR[dow] * _shape(len(hours), dow >= 5) * trend * f_ff * day_noise
                * rng.lognormal(0, 0.12, len(hours))
            )
            ff = rng.poisson(mean_ff)
            conv = np.clip(s.conv * (1.06 if dow >= 5 else 1.0) * f_conv, 0.02, 0.6)
            txn = rng.binomial(ff, conv)
            atv = s.atv * f_atv * rng.lognormal(0, 0.10, len(hours))
            sales = np.round(txn * atv, -1)
            for h, a, t, v in zip(hours, ff, txn, sales):
                rows.append((s.store_id, s.store_name, d + pd.Timedelta(hours=int(h)), int(a), int(t), float(v)))
    df = pd.DataFrame(rows, columns=["store_id", "store_name", "timestamp", "footfall", "transactions", "sales_inr"])
    return df


FIRST = [
    "Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Sneha", "Arjun", "Kavya", "Rahul", "Meera",
    "Karthik", "Divya", "Siddharth", "Pooja", "Nikhil", "Lakshmi", "Aditya", "Nandini", "Varun", "Ishita",
    "Harsha", "Swathi", "Manish", "Riya", "Tarun", "Shreya", "Akhil", "Deepika", "Sameer", "Tanvi",
    "Pranav", "Aisha", "Gaurav", "Neha", "Kiran", "Bhavya", "Suresh", "Farah", "Yash", "Sana",
    "Ravi", "Pallavi", "Imran", "Keerthi", "Dev", "Anjali", "Rakesh", "Sowmya", "Omkar", "Zoya",
]
LAST = list("ABCDEGHJKLMNPRSTVY")

# role, hourly cost (INR, fully loaded), skills, key holder
ROLES = {
    "Store Manager": (520, "sales_floor|cashier|visual_merch", True),
    "Assistant Manager": (380, "sales_floor|cashier|stockroom", True),
    "Shift Lead": (260, "sales_floor|cashier|fitting_room", True),
    "Senior Stylist": (230, "sales_floor|fitting_room", False),
    "Stylist": (175, "sales_floor|fitting_room", False),
    "Cashier": (165, "cashier|sales_floor", False),
    "Visual Merchandiser": (210, "visual_merch|sales_floor", False),
    "Stockroom Associate": (150, "stockroom|sales_floor", False),
}

# per store: list of (role, contract type)
TEAM = {
    "HYD01": [("Store Manager", "FT"), ("Assistant Manager", "FT"), ("Shift Lead", "FT"),
              ("Senior Stylist", "FT"), ("Stylist", "FT"), ("Stylist", "FT"), ("Stylist", "FT"),
              ("Cashier", "FT"), ("Cashier", "FT"), ("Visual Merchandiser", "FT"),
              ("Stockroom Associate", "FT"), ("Stylist", "PT"), ("Stylist", "PT"),
              ("Cashier", "PT"), ("Stylist", "PT")],
    "BLR01": [("Store Manager", "FT"), ("Assistant Manager", "FT"), ("Shift Lead", "FT"),
              ("Senior Stylist", "FT"), ("Senior Stylist", "FT"), ("Stylist", "FT"), ("Stylist", "FT"),
              ("Stylist", "FT"), ("Cashier", "FT"), ("Cashier", "FT"), ("Visual Merchandiser", "FT"),
              ("Stockroom Associate", "FT"), ("Stylist", "PT"), ("Stylist", "PT"), ("Cashier", "PT"),
              ("Stylist", "PT"), ("Cashier", "PT")],
    "MUM01": [("Store Manager", "FT"), ("Assistant Manager", "FT"), ("Shift Lead", "FT"),
              ("Shift Lead", "FT"), ("Senior Stylist", "FT"), ("Senior Stylist", "FT"), ("Stylist", "FT"),
              ("Stylist", "FT"), ("Stylist", "FT"), ("Stylist", "FT"), ("Cashier", "FT"), ("Cashier", "FT"),
              ("Visual Merchandiser", "FT"), ("Stockroom Associate", "FT"), ("Stylist", "PT"),
              ("Stylist", "PT"), ("Cashier", "PT"), ("Stylist", "PT"), ("Cashier", "PT")],
}


def generate_staff(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    names = rng.permutation(len(FIRST))
    rows, n = [], 0
    for _, s in STORES.iterrows():
        o, c = int(s.open_hour), int(s.close_hour)
        for i, (role, ctype) in enumerate(TEAM[s.store_id]):
            cost, skills, kh = ROLES[role]
            first = FIRST[names[n % len(FIRST)]]
            gender = "F" if names[n % len(FIRST)] % 2 else "M"  # FIRST alternates male / female names
            name = f"{first} {LAST[(n * 7) % len(LAST)]}."
            n += 1
            avail = {d: f"{o}-{c}" for d in DAYS}
            if ctype == "FT":
                contract, max_h = 45, 52
                if i % 5 == 4:  # some full-timers can't do late finishes on two weekdays
                    for d in rng.choice(DAYS[:5], 2, replace=False):
                        avail[d] = f"{o}-{min(c, o + 9)}"
            else:  # part-time: students / second jobs, mostly evenings + weekends
                contract, max_h = 24, 30
                for d in DAYS[:5]:
                    avail[d] = f"{max(o, c - 6)}-{c}"
                if rng.random() < 0.5:
                    avail[DAYS[rng.integers(0, 5)]] = "off"
            rows.append({
                "staff_id": f"{s.store_id}-{i + 1:02d}",
                "store_id": s.store_id,
                "name": name,
                "gender": gender,
                "role": role,
                "contract_hours": contract,
                "max_hours": max_h,
                "hourly_cost_inr": int(round(cost * rng.uniform(0.94, 1.06))),
                "key_holder": kh,
                "skills": skills,
                **{f"avail_{d}": avail[d] for d in DAYS},
            })
    return pd.DataFrame(rows)


def demo_bundle() -> dict:
    ev = events_frame()[["event", "start_date", "end_date"]]
    return {"demand": generate_demand(), "staff": generate_staff(), "events": ev}


# ---------------------------------------------------------------- CSV upload

DEMAND_COLS = ["store_id", "timestamp", "footfall", "transactions", "sales_inr"]
STAFF_COLS = ["staff_id", "store_id", "name", "role", "contract_hours", "max_hours",
              "hourly_cost_inr", "key_holder"] + [f"avail_{d}" for d in DAYS]


def clean_demand(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in DEMAND_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"demand file is missing columns: {', '.join(missing)}")
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if "store_name" not in df.columns:
        df["store_name"] = df["store_id"]
    for c in ["footfall", "transactions", "sales_inr"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df.sort_values(["store_id", "timestamp"]).reset_index(drop=True)


def clean_staff(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in STAFF_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"staff file is missing columns: {', '.join(missing)}")
    df = df.copy()
    df["key_holder"] = df["key_holder"].astype(str).str.strip().str.lower().isin(["true", "1", "yes", "y"])
    for c in ["contract_hours", "max_hours", "hourly_cost_inr"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    if "skills" not in df.columns:
        df["skills"] = ""
    return df


def clean_events(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in ["event", "start_date", "end_date"] if c not in df.columns]
    if missing:
        raise ValueError(f"events file is missing columns: {', '.join(missing)}")
    df = df.copy()
    df["start_date"] = pd.to_datetime(df["start_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    return df[["event", "start_date", "end_date"]]


def store_table(demand: pd.DataFrame) -> pd.DataFrame:
    """Store list with trading hours inferred from the hourly data."""
    d = demand.assign(hour=demand["timestamp"].dt.hour)
    g = d.groupby(["store_id", "store_name"])["hour"].agg(["min", "max"]).reset_index()
    g.columns = ["store_id", "store_name", "open_hour", "close_hour"]
    g["close_hour"] = g["close_hour"] + 1
    return g


def parse_window(value: str) -> tuple[int, int] | None:
    v = str(value).strip().lower()
    if v in ("", "off", "nan", "none", "-"):
        return None
    a, b = v.split("-")
    return int(a), int(b)
