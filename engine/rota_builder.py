"""Staffing requirement + greedy rota builder.

Requirement per trading hour:
    required = max(min_staff, ceil(forecast footfall / customers_per_staff_hour))

Rota builder (greedy, explainable):
  1. Key-holder pass: every day gets a key-holder on an opening shift and one on
     a closing shift.
  2. Gap pass: repeatedly take the hour with the biggest shortfall and pick the
     best shift that covers it, from staff who are available, under their weekly
     hour cap, under the max working days, and have enough rest since their last
     shift. A shift's score rewards hours that fill real gaps and penalises idle
     hours and overtime.
     It can also stretch a shift someone already works that day. Full-timers
     get full-length shifts; part-timers cover short peaks.
  3. Rebalance: move overtime shifts to colleagues with spare contracted hours,
     then trim remaining overtime off shift ends that are already over-covered.
  4. Whatever cannot be filled is reported as an understaffed hour.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .demo_data import DAYS, DAY_LABELS, parse_window


@dataclass
class Rules:
    customers_per_staff_hour: float = 10.0
    min_staff: int = 3
    min_shift_hours: int = 4
    max_shift_hours: int = 9
    min_rest_hours: int = 12
    max_days_per_week: int = 6
    overtime_multiplier: float = 2.0
    weekly_hours_cap: int = 52
    ft_min_shift_hours: int = 7  # full-timers (35h+ contracts) work full shifts; part-timers cover short peaks

    def as_dict(self) -> dict:
        return asdict(self)


def requirement(fc_week: pd.DataFrame, rules: Rules, open_hour: int, close_hour: int,
                uplift_pct: float = 0.0) -> np.ndarray:
    """7 x H array of staff required per trading hour for one Mon-Sun week."""
    hours = list(range(open_hour, close_hour))
    ff = (fc_week.pivot_table(index="dow", columns="hour", values="footfall", aggfunc="sum")
          .reindex(index=range(7), columns=hours).fillna(0).to_numpy())
    ff = ff * (1 + uplift_pct / 100)
    req = np.ceil(ff / max(rules.customers_per_staff_hour, 0.1))
    return np.maximum(req, rules.min_staff).astype(int)


@dataclass
class _Person:
    idx: int
    staff_id: str
    name: str
    role: str
    contract: float
    max_hours: float
    rate: float
    key_holder: bool
    avail: list  # per day: (start, end) or None
    hours: float = 0.0
    shifts: dict = None  # day -> (start, end)

    def __post_init__(self):
        self.shifts = {}


def _people(staff: pd.DataFrame, rules: Rules) -> list[_Person]:
    out = []
    for i, r in enumerate(staff.itertuples(index=False)):
        avail = [parse_window(getattr(r, f"avail_{d}")) for d in DAYS]
        out.append(_Person(i, r.staff_id, r.name, r.role, float(r.contract_hours),
                           float(min(r.max_hours, rules.weekly_hours_cap)), float(r.hourly_cost_inr),
                           bool(r.key_holder), avail))
    return out


def _can_work(p: _Person, day: int, start: int, end: int, rules: Rules) -> bool:
    if day in p.shifts or len(p.shifts) >= rules.max_days_per_week:
        return False
    w = p.avail[day]
    if w is None or start < w[0] or end > w[1]:
        return False
    if p.hours + (end - start) > p.max_hours:
        return False
    prev = p.shifts.get(day - 1)
    if prev and (24 - prev[1]) + start < rules.min_rest_hours:
        return False
    nxt = p.shifts.get(day + 1)
    if nxt and (24 - end) + nxt[0] < rules.min_rest_hours:
        return False
    return True


def _can_extend(p: _Person, day: int, start: int, end: int, rules: Rules) -> bool:
    """Can this person's existing shift today grow to [start, end)?"""
    S, E = p.shifts[day]
    w = p.avail[day]
    if w is None or start < w[0] or end > w[1] or end - start > rules.max_shift_hours:
        return False
    if p.hours + (end - start) - (E - S) > p.max_hours:
        return False
    prev = p.shifts.get(day - 1)
    if prev and (24 - prev[1]) + start < rules.min_rest_hours:
        return False
    nxt = p.shifts.get(day + 1)
    if nxt and (24 - end) + nxt[0] < rules.min_rest_hours:
        return False
    return True


def _score(p: _Person, gap_row: np.ndarray, s: int, e: int, rules: Rules) -> tuple[float, int]:
    seg = gap_row[s:e]
    useful = int((seg > 0).sum())
    idle = (e - s) - useful
    ot = max(0.0, p.hours + (e - s) - p.contract) - max(0.0, p.hours - p.contract)
    within_contract = (e - s) - ot
    score = useful - 0.6 * idle - 0.8 * ot * (rules.overtime_multiplier - 1 + 0.5) \
        + 0.15 * within_contract - 0.0005 * p.rate * (e - s)
    return score, useful


def build_rota(required: np.ndarray, staff: pd.DataFrame, rules: Rules,
               open_hour: int, close_hour: int) -> dict:
    H = close_hour - open_hour
    people = _people(staff, rules)
    sched = np.zeros_like(required)
    alerts: list[str] = []
    lengths = list(range(rules.min_shift_hours, min(rules.max_shift_hours, H) + 1))

    def lengths_for(p: _Person) -> list[int]:
        full_timer = p.contract >= 35 and (p.contract - p.hours) >= rules.ft_min_shift_hours
        lo = max(rules.min_shift_hours, rules.ft_min_shift_hours) if full_timer else rules.min_shift_hours
        return [L for L in lengths if L >= lo] or lengths[-1:]

    def assign(p: _Person, day: int, s: int, e: int):  # s, e are 0-based offsets
        p.shifts[day] = (open_hour + s, open_hour + e)
        p.hours += e - s
        sched[day, s:e] += 1

    # 1. key-holder pass: opener and closer every day
    for day in range(7):
        for kind in ("open", "close"):
            best = None
            for p in people:
                if not p.key_holder:
                    continue
                for L in sorted(lengths_for(p), reverse=True):
                    s, e = (0, L) if kind == "open" else (H - L, H)
                    if not _can_work(p, day, open_hour + s, open_hour + e, rules):
                        continue
                    gap = required[day] - sched[day]
                    sc, _ = _score(p, gap, s, e, rules)
                    sc += 0.02 * (p.contract - p.hours)  # spread key-holder shifts evenly
                    if best is None or sc > best[0]:
                        best = (sc, p, s, e)
            if best:
                assign(best[1], day, best[2], best[3])
            else:
                alerts.append(f"No key-holder available to {kind} the store on {DAY_LABELS[day]}")

    # 2. greedy gap pass
    blocked = np.zeros_like(required, dtype=bool)
    for _ in range(2000):
        gap = required - sched
        gap_masked = np.where(blocked, -999, gap)
        if gap_masked.max() <= 0:
            break
        day, h = np.unravel_index(int(np.argmax(gap_masked)), gap.shape)
        best = None
        for p in people:
            if day in p.shifts:  # option A: stretch the shift this person already has today
                S, E = p.shifts[day]
                hh = open_hour + h
                ns, ne = (hh, E) if hh < S else (S, hh + 1) if hh >= E else (None, None)
                if ns is None or not _can_extend(p, day, ns, ne, rules):
                    continue
                a_s, a_e = (ns, S) if ns < S else (E, ne)  # the added hours
                sc, useful = _score(p, gap[day], a_s - open_hour, a_e - open_hour, rules)
                sc += 0.3 * (a_e - a_s)  # no extra working day or commute
                if useful >= 1 and (best is None or sc > best[0]):
                    best = (sc, p, ns - open_hour, ne - open_hour, True)
                continue
            for L in lengths_for(p):  # option B: a new shift
                for s in range(max(0, h - L + 1), min(h, H - L) + 1):
                    e = s + L
                    if not _can_work(p, day, open_hour + s, open_hour + e, rules):
                        continue
                    sc, useful = _score(p, gap[day], s, e, rules)
                    if useful < 1:
                        continue
                    if best is None or sc > best[0]:
                        best = (sc, p, s, e, False)
        if best is None:
            blocked[day, h] = True
            continue
        _, p, s, e, extend = best
        if extend:
            S, E = p.shifts.pop(day)
            p.hours -= E - S
            sched[day, S - open_hour:E - open_hour] -= 1
        assign(p, day, s, e)

    # 2b. rebalance: hand overtime shifts to colleagues who still have contracted hours free
    def unassign(p: _Person, day: int):
        s, e = p.shifts.pop(day)
        p.hours -= e - s
        sched[day, s - open_hour:e - open_hour] -= 1
        return s, e

    moved = 0
    for _ in range(200):
        changed = False
        for a in sorted(people, key=lambda x: x.contract - x.hours):  # most overtime first
            if a.hours <= a.contract:
                break
            for day, (s, e) in sorted(a.shifts.items(), key=lambda kv: kv[1][0] - kv[1][1]):
                needs_key = a.key_holder and (s == open_hour or e == close_hour)
                takers = sorted((b for b in people if b is not a and (b.key_holder or not needs_key)
                                 and b.hours + (e - s) <= b.contract),
                                key=lambda b: (b.hours - b.contract, b.rate))
                b = next((b for b in takers if _can_work(b, day, s, e, rules)), None)
                if b:
                    unassign(a, day)
                    assign(b, day, s - open_hour, e - open_hour)
                    moved += 1
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break

    # 2c. trim overtime: shave overtime hours off shift ends where that hour is already over-covered
    for p in people:
        min_len = rules.min_shift_hours
        while p.hours > p.contract:
            options = []
            for day, (s, e) in p.shifts.items():
                if e - s <= min_len:
                    continue
                kh_open = p.key_holder and s == open_hour
                kh_close = p.key_holder and e == close_hour
                if not kh_open and sched[day, s - open_hour] > required[day, s - open_hour]:
                    options.append((sched[day, s - open_hour] - required[day, s - open_hour], day, "start"))
                if not kh_close and sched[day, e - 1 - open_hour] > required[day, e - 1 - open_hour]:
                    options.append((sched[day, e - 1 - open_hour] - required[day, e - 1 - open_hour], day, "end"))
            if not options:
                break
            _, day, side = max(options)
            s, e = p.shifts[day]
            if side == "start":
                sched[day, s - open_hour] -= 1
                p.shifts[day] = (s + 1, e)
            else:
                sched[day, e - 1 - open_hour] -= 1
                p.shifts[day] = (s, e - 1)
            p.hours -= 1

    # 3. outputs
    shift_rows, staff_rows = [], []
    for p in people:
        for day, (s, e) in sorted(p.shifts.items()):
            shift_rows.append({"staff_id": p.staff_id, "name": p.name, "role": p.role, "day": DAY_LABELS[day],
                               "day_idx": day, "start": s, "end": e, "hours": e - s, "key_holder": p.key_holder})
        ot = max(0.0, p.hours - p.contract)
        cost = (p.hours - ot) * p.rate + ot * p.rate * rules.overtime_multiplier
        staff_rows.append({"staff_id": p.staff_id, "name": p.name, "role": p.role,
                           "key_holder": p.key_holder, "contract_hours": p.contract,
                           "scheduled_hours": p.hours, "overtime_hours": ot,
                           "utilisation_pct": 100 * p.hours / p.contract if p.contract else 0.0,
                           "days_worked": len(p.shifts), "hourly_cost_inr": p.rate, "labour_cost_inr": cost,
                           **{DAY_LABELS[d]: (f"{p.shifts[d][0]:02d}-{p.shifts[d][1]:02d}" if d in p.shifts else "OFF")
                              for d in range(7)}})
    gap = required - sched
    for day in range(7):
        short = [h for h in range(H) if gap[day, h] > 0]
        if short:
            ranges, a = [], short[0]  # merge consecutive short hours into time ranges
            for prev, cur in zip(short, short[1:] + [None]):
                if cur != prev + 1:
                    ranges.append(f"{open_hour + a:02d}:00–{open_hour + prev + 1:02d}:00")
                    a = cur
            alerts.append(f"{DAY_LABELS[day]}: {int(gap[day][gap[day] > 0].sum())} staff-hours short "
                          f"({', '.join(ranges)})")
    return {"scheduled": sched, "required": required, "shifts": pd.DataFrame(shift_rows),
            "staff": pd.DataFrame(staff_rows), "alerts": alerts}
