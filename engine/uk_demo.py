"""Builds sample_data/uk_3site_retail_demo.csv: one week, 3 UK stores, 24 staff.

Fictional retailer "Loom & Lane UK". Written as a Planday-style export (semicolons,
UK day-first dates) so the one-click demo also exercises the real importer.

Deliberate traps (so a first-time visitor sees value in seconds):
  - Adult WTR: cross-site clopening (Rahim: closes Covent Garden 22:00 Tue, opens
    Shoreditch 07:00 Wed), same-site clopening of 10.5h (Ellie), 7h shift with no break (Mark)
  - Under-18 rest break: 4h40m worked with a 20-minute break (Maisie)
  - NMW: Jordan turned 21 on 16 Nov 2026 but is still paid £11.50
  - Night work: overnight stock-take 22:00-06:00 (Tomasz)
  - Cost: three advisors on time-and-a-half overtime while casual / part-time
    colleagues at the same store have spare hours

Run `python -m engine.uk_demo` to regenerate the CSV.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

WEEK_START = date(2026, 11, 30)  # Monday
SITE_SALES = {"Covent Garden": 42000.0, "Shoreditch": 33000.0, "Croydon": 24000.0}  # weekly sales, £
DEMO_FILE = Path(__file__).resolve().parent.parent / "sample_data" / "uk_3site_retail_demo.csv"

# shift shorthands: (start, end, unpaid break minutes)
O = ("07:30", "17:00", 30)  # opening, 9h paid
O8 = ("07:30", "16:00", 30)  # opening, 8h
M = ("10:00", "18:30", 30)  # middle, 8h
C = ("13:30", "22:00", 30)  # closing (CG/SD close 21:00 + close-down), 8h
SH = ("16:00", "21:00", 0)  # short evening, 5h
SUN = ("11:30", "17:30", 0)  # Sunday 6h
CR_O = ("07:00", "16:30", 30)  # Croydon opening, 9h
CR_M = ("09:30", "18:00", 30)
CR_C = ("12:00", "20:30", 30)

D = dict(Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6)

# id, name, site, role, dob, contract, rate, contracted hours, opt-out, shifts {day: shift or (shift, site)}
STAFF = [
    # ---- Covent Garden
    ("LL001", "Sophie Turner", "Covent Garden", "Store Manager", "1988-05-02", "Permanent", 17.80, 45, "Yes",
     {"Mon": O, "Tue": O, "Wed": ("10:00", "19:30", 30), "Fri": ("10:00", "19:30", 30), "Sat": O}),
    ("LL002", "Daniel Okafor", "Covent Garden", "Assistant Manager", "1993-09-21", "Permanent", 15.20, 40, "No",
     {"Tue": C, "Wed": M, "Thu": C, "Sat": C, "Sun": ("11:00", "19:00", 30)}),
    ("LL003", "Hannah Price", "Covent Garden", "Sales Advisor", "1998-01-11", "Permanent", 12.95, 37.5, "No",
     {"Mon": M, "Tue": M, "Thu": M, "Sat": M, "Sun": SUN}),
    ("LL004", "Liam Walsh", "Covent Garden", "Sales Advisor", "2001-07-30", "Permanent", 12.95, 30, "No",
     {"Mon": C, "Wed": C, "Fri": M, "Sat": SH}),
    ("LL005", "Amira Haddad", "Covent Garden", "Sales Advisor", "2007-03-14", "Permanent", 11.20, 24, "No",
     {"Thu": SH, "Fri": C, "Sat": M}),
    ("LL006", "Maisie Clarke", "Covent Garden", "Weekend Advisor", "2009-06-02", "Permanent", 8.40, 12, "No",
     {"Sat": ("10:00", "15:00", 20), "Sun": ("12:00", "16:00", 0)}),
    ("LL007", "Ben Harris", "Covent Garden", "Sales Advisor", "1996-12-05", "Zero hours", 12.71, None, "No",
     {"Fri": SH}),
    ("LL008", "Tomasz Nowak", "Covent Garden", "Stockroom Associate", "1990-02-17", "Permanent", 13.10, 37.5, "No",
     {"Mon": ("06:00", "14:00", 30), "Tue": ("06:00", "14:00", 30), "Wed": ("06:00", "14:00", 30),
      "Thu": ("22:00", "06:00", 30), "Sat": ("06:00", "14:00", 30)}),
    # ---- Shoreditch
    ("LL009", "Grace Mensah", "Shoreditch", "Store Manager", "1987-10-08", "Permanent", 17.50, 45, "Yes",
     {"Mon": O, "Tue": O, "Wed": O, "Fri": O, "Sat": O}),
    ("LL010", "Ellie Robinson", "Shoreditch", "Assistant Manager", "1994-04-25", "Permanent", 15.00, 40, "No",
     {"Mon": C, "Tue": C, "Thu": M, "Fri": ("12:30", "21:00", 30), "Sat": O8}),
    ("LL011", "Kai Thompson", "Shoreditch", "Sales Advisor", "1999-08-19", "Permanent", 12.85, 37.5, "No",
     {"Mon": M, "Tue": M, "Wed": M, "Thu": M, "Fri": ("16:00", "20:00", 0), "Sat": M}),
    ("LL012", "Priya Sharma", "Shoreditch", "Sales Advisor", "2000-02-03", "Permanent", 12.85, 37.5, "No",
     {"Mon": O8, "Wed": C, "Thu": C, "Fri": M, "Sat": C, "Sun": SUN}),
    ("LL013", "Jordan Lee", "Shoreditch", "Sales Advisor", "2005-11-16", "Permanent", 11.50, 30, "No",
     {"Tue": SH, "Thu": M, "Sat": M, "Sun": SUN}),
    ("LL014", "Chloe Evans", "Shoreditch", "Visual Merchandiser", "1997-06-12", "Permanent", 13.40, 30, "No",
     {"Mon": M, "Tue": M, "Wed": M, "Thu": SH}),
    ("LL015", "Noah Bennett", "Shoreditch", "Sales Advisor", "1995-03-27", "Zero hours", 12.71, None, "No",
     {"Wed": SH}),
    ("LL016", "Zara Ali", "Shoreditch", "Sales Advisor", "2002-10-09", "Zero hours", 12.71, None, "No",
     {"Sun": ("12:00", "16:00", 0)}),
    # ---- Croydon
    ("LL017", "Mark Davies", "Croydon", "Store Manager", "1985-01-30", "Permanent", 16.90, 45, "Yes",
     {"Mon": CR_O, "Tue": CR_O, "Thu": CR_O, "Fri": CR_O, "Sun": ("10:30", "17:30", 0)}),
    ("LL018", "Fatima Begum", "Croydon", "Assistant Manager", "1992-07-14", "Permanent", 14.60, 40, "No",
     {"Mon": CR_C, "Wed": ("07:30", "16:00", 30), "Thu": CR_C, "Fri": CR_C, "Sat": ("07:30", "16:00", 30)}),
    ("LL019", "Ryan Cooper", "Croydon", "Sales Advisor", "1999-11-22", "Permanent", 12.71, 37.5, "No",
     {"Mon": CR_M, "Tue": CR_M, "Wed": CR_M, "Fri": CR_M, "Sat": CR_M, "Sun": ("11:00", "16:00", 0)}),
    ("LL020", "Olivia Green", "Croydon", "Sales Advisor", "1998-05-06", "Permanent", 12.71, 24, "No",
     {"Tue": CR_C, "Thu": ("15:00", "20:00", 0)}),
    ("LL021", "Ethan Wright", "Croydon", "Apprentice Sales Advisor", "2008-04-10", "Apprentice", 8.00, 32, "No",
     {"Mon": CR_M, "Wed": CR_M, "Fri": CR_M, "Sat": CR_M}),
    ("LL022", "Ruby Hughes", "Croydon", "Weekend Advisor", "2009-09-20", "Permanent", 8.00, 8, "No",
     {"Sat": ("10:00", "14:00", 0), "Sun": ("11:00", "15:00", 0)}),
    ("LL023", "Sam Patel", "Croydon", "Sales Advisor", "1997-08-01", "Zero hours", 12.71, None, "No",
     {"Thu": ("15:00", "20:00", 0)}),
    # ---- multi-site floater
    ("LL024", "Rahim Uddin", "Covent Garden", "Sales Lead", "1995-12-19", "Permanent", 14.20, 37.5, "No",
     {"Mon": M, "Tue": C, "Wed": (("07:00", "15:30", 30), "Shoreditch"), "Thu": (CR_M, "Croydon"),
      "Sat": (M, "Shoreditch")}),
]

HEADER = ["Employee number", "Employee", "Department", "Employee group", "Date of birth", "Contract",
          "Contracted hours", "48h opt-out", "Date", "From", "To", "Break", "Wage"]


def rows() -> list[list[str]]:
    out = []
    for emp_id, name, site, role, dob, contract, rate, hours, opt, shifts in STAFF:
        y, m, d = (int(x) for x in dob.split("-"))
        dob_uk = f"{d:02d}/{m:02d}/{y}"
        for day, spec in sorted(shifts.items(), key=lambda kv: D[kv[0]]):
            (start, end, brk), shift_site = (spec, site) if isinstance(spec[0], str) else spec
            dt = WEEK_START + timedelta(days=D[day])
            out.append([emp_id, name, shift_site, role, dob_uk, contract, "" if hours is None else f"{hours:g}",
                        opt, f"{dt.day:02d}/{dt.month:02d}/{dt.year}", start, end, str(brk), f"{rate:.2f}"])
    return out


def write(path: Path = DEMO_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(HEADER)
        w.writerows(rows())
    return path


if __name__ == "__main__":
    p = write()
    print(f"wrote {p} ({len(rows())} shifts)")
