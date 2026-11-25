"""Versioned UK statutory rates and Working Time Regulations limits.

Every number the UK engine uses lives here, so a new tax year is a one-file change.
Sources (checked November 2026):
  - National Minimum Wage / Living Wage: https://www.gov.uk/national-minimum-wage-rates
    April 2026 rates; April 2027 rates announced 28 Oct 2026 (LPC recommendations accepted).
  - Employer NI 2026-27: https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027
  - SSP from 6 April 2026 (day-one, lower of flat rate or 80% of AWE):
    https://www.acas.org.uk/statutory-sick-pay-changes-2026
  - Irregular-hours holiday accrual 12.07%:
    https://www.gov.uk/government/publications/simplifying-holiday-entitlement-and-holiday-pay-calculations
  - Working Time Regulations: https://www.gov.uk/rest-breaks-work, https://www.gov.uk/night-working-hours
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date


@dataclass(frozen=True)
class UKRates:
    version: str
    label: str
    nmw_effective_from: date  # minimum wage changes on 1 April
    ni_effective_from: date  # NI and SSP change on 6 April
    nlw_21_plus: float  # £/hour
    nmw_18_20: float
    nmw_16_17: float
    nmw_apprentice: float  # under-19s, or 19+ in the first year of an apprenticeship
    employer_ni_rate: float  # Class 1 secondary
    ni_secondary_threshold_weekly: float  # ST: £5,000 a year
    ni_upper_secondary_threshold_weekly: float  # UST (under-21s) and AUST (apprentices under 25)
    holiday_accrual_irregular: float  # 5.6 / 46.4 weeks, for irregular-hours / part-year workers
    ssp_weekly: float
    ssp_awe_share: float  # SSP is the lower of the flat rate and this share of average weekly earnings
    status: str  # "in force" or "announced"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["nmw_effective_from"] = self.nmw_effective_from.isoformat()
        d["ni_effective_from"] = self.ni_effective_from.isoformat()
        return d


UK_2026_27 = UKRates(
    version="2026-27",
    label="2026/27 (in force from April 2026)",
    nmw_effective_from=date(2026, 4, 1),
    ni_effective_from=date(2026, 4, 6),
    nlw_21_plus=12.71,
    nmw_18_20=10.85,
    nmw_16_17=8.00,
    nmw_apprentice=8.00,
    employer_ni_rate=0.15,
    ni_secondary_threshold_weekly=96.00,
    ni_upper_secondary_threshold_weekly=967.00,
    holiday_accrual_irregular=0.1207,
    ssp_weekly=123.25,
    ssp_awe_share=0.80,
    status="in force",
)

# Minimum wage rates announced on 28 Oct 2026 for 1 April 2027. NI thresholds are frozen;
# the 2027 SSP rate has not been announced, so the 2026/27 figure is carried forward.
UK_2027_28_PREVIEW = UKRates(
    version="2027-28-preview",
    label="2027/28 preview (announced, from April 2027)",
    nmw_effective_from=date(2027, 4, 1),
    ni_effective_from=date(2027, 4, 6),
    nlw_21_plus=13.21,
    nmw_18_20=11.27,
    nmw_16_17=8.31,
    nmw_apprentice=8.31,
    employer_ni_rate=0.15,
    ni_secondary_threshold_weekly=96.00,
    ni_upper_secondary_threshold_weekly=967.00,
    holiday_accrual_irregular=0.1207,
    ssp_weekly=123.25,
    ssp_awe_share=0.80,
    status="announced",
)

RATE_TABLES = {r.version: r for r in (UK_2026_27, UK_2027_28_PREVIEW)}
DEFAULT_RATES = UK_2026_27


@dataclass(frozen=True)
class WTRLimits:
    """Working Time Regulations 1998 limits used by engine/wtr_rules.py."""
    adult_break_after_hours: float = 6.0  # more than 6 hours worked -> 20-minute break
    adult_min_break_mins: int = 20
    adult_daily_rest_hours: float = 11.0
    adult_weekly_rest_hours: float = 24.0  # uninterrupted, in each 7-day period
    adult_weekly_hours_limit: float = 48.0  # legal limit is an average over 17 weeks; one week is an early warning
    young_break_after_hours: float = 4.5  # under-18s: more than 4.5 hours -> 30-minute break
    young_min_break_mins: int = 30
    young_daily_rest_hours: float = 12.0
    young_weekly_rest_hours: float = 48.0
    young_daily_hours_limit: float = 8.0
    young_weekly_hours_limit: float = 40.0
    night_start_hour: int = 23  # default night time 23:00-06:00
    night_end_hour: int = 6
    night_min_hours: float = 3.0  # 3+ hours in night time -> night shift
    holiday_record_retention_years: int = 6  # holiday records, from 6 April 2026
    hours_record_retention_years: int = 2  # working-time (48-hour) records

    def as_dict(self) -> dict:
        return asdict(self)


WTR = WTRLimits()

TIMEZONE = "Europe/London"
