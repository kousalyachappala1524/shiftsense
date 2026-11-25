"""Explainable demand forecast.

forecast(hour) = recent day-of-week x hour profile  x  recent level  x  event uplift

* Profile: average of the last 8 normal (non-event) weeks for each weekday/hour.
* Level: how the last 4 normal weeks compare with the last 8 (damped by half).
* Event uplift: learned from the same event in history (e.g. last year's
  Black Friday vs. the 8 normal weeks before it). Events with no history get 1.0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PROFILE_WEEKS = 8
LEVEL_WEEKS = 4


def _event_days(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, e in events.iterrows():
        for d in pd.date_range(e.start_date, e.end_date, freq="D"):
            rows.append((d.normalize(), e.event))
    return pd.DataFrame(rows, columns=["date", "event"]).drop_duplicates("date")


def _prep(hist: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    h = hist.copy()
    h["date"] = h["timestamp"].dt.normalize()
    h["dow"] = h["timestamp"].dt.day_of_week
    h["hour"] = h["timestamp"].dt.hour
    ed = _event_days(events)
    h = h.merge(ed, on="date", how="left")
    return h


def _uplifts(h: pd.DataFrame) -> dict:
    """Per-event ratios vs. the same weekday in the 8 normal weeks before each occurrence."""
    daily = h.groupby(["date", "dow"], as_index=False).agg(
        footfall=("footfall", "sum"), transactions=("transactions", "sum"),
        sales=("sales_inr", "sum"), event=("event", "first"))
    normal = daily[daily["event"].isna()]
    out: dict[str, dict] = {}
    ev_days = daily[daily["event"].notna()]
    for name, grp in ev_days.groupby("event"):
        r_ff, r_conv, r_spv = [], [], []
        for _, d in grp.iterrows():
            ref = normal[(normal["dow"] == d["dow"]) & (normal["date"] < d["date"])
                         & (normal["date"] >= d["date"] - pd.Timedelta(days=56))]
            if len(ref) < 2 or ref["footfall"].mean() <= 0:
                continue
            base_ff = ref["footfall"].mean()
            base_conv = ref["transactions"].sum() / max(ref["footfall"].sum(), 1)
            base_spv = ref["sales"].sum() / max(ref["footfall"].sum(), 1)
            r_ff.append(d["footfall"] / base_ff)
            if d["footfall"] > 0:
                r_conv.append((d["transactions"] / d["footfall"]) / base_conv)
                r_spv.append((d["sales"] / d["footfall"]) / base_spv)
        if r_ff:
            out[name] = {"footfall": float(np.median(r_ff)), "conversion": float(np.median(r_conv)),
                         "sales_per_visitor": float(np.median(r_spv)), "days_seen": len(r_ff)}
    return out


def fit(hist: pd.DataFrame, events: pd.DataFrame) -> dict:
    h = _prep(hist, events)
    last = h["date"].max()
    normal = h[h["event"].isna()]
    recent = normal[normal["date"] > last - pd.Timedelta(weeks=PROFILE_WEEKS)]
    if recent["date"].nunique() < 14:  # not enough normal days -> use all normal data
        recent = normal
    prof = recent.groupby(["dow", "hour"]).agg(
        footfall=("footfall", "mean"), transactions=("transactions", "sum"),
        sales=("sales_inr", "sum"), ff_sum=("footfall", "sum")).reset_index()
    prof["conv"] = prof["transactions"] / prof["ff_sum"].clip(lower=1)
    prof["spv"] = prof["sales"] / prof["ff_sum"].clip(lower=1)
    lvl_recent = recent[recent["date"] > last - pd.Timedelta(weeks=LEVEL_WEEKS)]["footfall"].mean()
    lvl_all = recent["footfall"].mean()
    level = 1.0 if not lvl_all else 1 + 0.5 * (lvl_recent / lvl_all - 1)
    return {"profile": prof, "level": float(level), "uplifts": _uplifts(h), "last_date": last}


def predict(model: dict, events: pd.DataFrame, start: pd.Timestamp, days: int) -> pd.DataFrame:
    prof = model["profile"]
    ed = _event_days(events)
    dates = pd.date_range(start, periods=days, freq="D")
    grid = pd.DataFrame({"date": dates})
    grid["dow"] = grid["date"].dt.day_of_week
    out = grid.merge(prof[["dow", "hour", "footfall", "conv", "spv"]], on="dow", how="left")
    out = out.merge(ed, on="date", how="left")
    up = model["uplifts"]

    def factor(ev, key):
        return up.get(ev, {}).get(key, 1.0) if isinstance(ev, str) else 1.0

    out["f_ff"] = out["event"].map(lambda e: factor(e, "footfall"))
    out["f_conv"] = out["event"].map(lambda e: factor(e, "conversion"))
    out["f_spv"] = out["event"].map(lambda e: factor(e, "sales_per_visitor"))
    out["footfall"] = out["footfall"] * model["level"] * out["f_ff"]
    out["transactions"] = out["footfall"] * out["conv"] * out["f_conv"]
    out["sales_inr"] = out["footfall"] * out["spv"] * out["f_spv"]
    out["timestamp"] = out["date"] + pd.to_timedelta(out["hour"], unit="h")
    return out[["timestamp", "date", "dow", "hour", "footfall", "transactions", "sales_inr", "event"]] \
        .sort_values("timestamp").reset_index(drop=True)


def backtest(hist: pd.DataFrame, events: pd.DataFrame, days: int = 28) -> dict:
    """Hold out the last `days` of actuals, forecast them, and score the result."""
    last = hist["timestamp"].dt.normalize().max()
    cutoff = last - pd.Timedelta(days=days)
    train = hist[hist["timestamp"] < cutoff + pd.Timedelta(days=1)]
    test = hist[hist["timestamp"] >= cutoff + pd.Timedelta(days=1)].copy()
    model = fit(train, events)
    fc = predict(model, events, cutoff + pd.Timedelta(days=1), days)
    test["date"] = test["timestamp"].dt.normalize()
    act_d = test.groupby("date")[["footfall", "sales_inr"]].sum()
    fc_d = fc.groupby("date")[["footfall", "sales_inr"]].sum()
    j = act_d.merge(fc_d, left_index=True, right_index=True, suffixes=("_act", "_fc")).dropna()
    mape_ff = float((abs(j.footfall_fc - j.footfall_act) / j.footfall_act.clip(lower=1)).mean() * 100)
    mape_sales = float((abs(j.sales_inr_fc - j.sales_inr_act) / j.sales_inr_act.clip(lower=1)).mean() * 100)
    hj = test.merge(fc[["timestamp", "footfall"]], on="timestamp", suffixes=("_act", "_fc"))
    wape_hourly = float(abs(hj.footfall_fc - hj.footfall_act).sum() / max(hj.footfall_act.sum(), 1) * 100)
    daily = j.reset_index().rename(columns={"date": "date"})
    return {"mape_daily_footfall": mape_ff, "mape_daily_sales": mape_sales,
            "wape_hourly_footfall": wape_hourly, "daily": daily,
            "start": cutoff + pd.Timedelta(days=1), "end": last}
