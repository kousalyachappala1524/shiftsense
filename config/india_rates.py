"""India working-hours presets under the Labour Codes (in force since 21 Nov 2025).

The Occupational Safety, Health and Working Conditions Code sets the national frame:
8 working hours a day, 48 a week, overtime only with the worker's consent and paid at
twice the ordinary rate of wages, women's work before 06:00 / after 19:00 only with
consent and safety arrangements. The OSH (Central) Rules, notified 8 May 2026, cap
overtime at 144 hours a quarter for the central sphere.

Most private employers fall under STATE rules, and several states were still on draft
or transitional rules in late 2026. The state presets below are planning defaults taken
from published commentary, not legal advice: every value is editable in the app, and
each preset says how confident we are in it. Always check the current state notification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class IndiaPreset:
    name: str
    daily_hours: float  # maximum working hours a day (excluding rest intervals)
    weekly_hours: float
    ot_quarter_cap: float  # maximum overtime hours in a quarter
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


PRESETS = {
    "Central sphere": IndiaPreset("Central sphere", 8, 48, 144,
                                  "OSH (Central) Rules 2026: 144 hours of overtime a quarter."),
    "Telangana": IndiaPreset("Telangana", 8, 48, 144,
                             "8 hours a day. State overtime cap not confirmed: central 144 used as a reference."),
    "Karnataka": IndiaPreset("Karnataka", 9, 48, 50,
                             "Commentary reports 9 hours a day and 50 overtime hours a quarter. Verify."),
    "Maharashtra": IndiaPreset("Maharashtra", 9, 48, 125,
                               "Commentary reports 9 hours a day and 125 overtime hours a quarter. Verify."),
    "Tamil Nadu": IndiaPreset("Tamil Nadu", 8, 48, 144,
                              "8 hours a day. State overtime cap not confirmed: central 144 used as a reference."),
    "Gujarat": IndiaPreset("Gujarat", 9, 48, 125,
                           "Commentary reports 9 hours a day and 125 overtime hours a quarter. Verify."),
    "Haryana": IndiaPreset("Haryana", 9, 48, 50,
                           "Commentary reports 9 hours a day and 50 overtime hours a quarter. Verify."),
    "Delhi": IndiaPreset("Delhi", 9, 48, 144,
                         "State rules still in draft. 9 hours a day assumed; central 144 cap used as a reference."),
}

# Demo store city -> default state preset
CITY_STATE = {"Hyderabad": "Telangana", "Bengaluru": "Karnataka", "Mumbai": "Maharashtra",
              "Chennai": "Tamil Nadu", "Ahmedabad": "Gujarat", "Gurugram": "Haryana", "Delhi": "Delhi"}

OT_PAY_MULTIPLIER = 2.0  # twice the ordinary rate of wages
REST_INTERVAL_AFTER_HOURS = 5.0  # no more than 5 hours' work without a rest interval
REST_INTERVAL_HOURS = 1.0  # rest interval assumed inside shifts longer than 5 hours (legal minimum 30 min)
WEEKLY_DAYS_OFF = 1  # at least one day off in seven
WOMEN_NIGHT_START_HOUR = 19  # work after 19:00 or before 06:00 needs consent + safety/transport arrangements
WOMEN_NIGHT_END_HOUR = 6
WEEKS_PER_QUARTER = 13
TIMEZONE = "Asia/Kolkata"
