"""Glue: data -> forecast -> requirement -> rota -> KPIs for one store-week."""

from __future__ import annotations

import pandas as pd

from . import forecaster as fc
from .demo_data import store_table
from .kpis import daily_table, week_kpis
from .rota_builder import Rules, build_rota, requirement

HORIZON_DAYS = 28


def store_forecast(demand: pd.DataFrame, events: pd.DataFrame, store_id: str) -> dict:
    hist = demand[demand["store_id"] == store_id]
    model = fc.fit(hist, events)
    start = model["last_date"] + pd.Timedelta(days=1)
    # start the planning horizon on the next Monday so every week is Mon-Sun
    start = start + pd.Timedelta(days=(7 - start.day_of_week) % 7)
    pred = fc.predict(model, events, start, HORIZON_DAYS)
    bt = fc.backtest(hist, events, days=28)
    return {"model": model, "forecast": pred, "backtest": bt, "start": start, "history": hist}


def week_slice(pred: pd.DataFrame, week_start: pd.Timestamp) -> pd.DataFrame:
    end = week_start + pd.Timedelta(days=7)
    return pred[(pred["date"] >= week_start) & (pred["date"] < end)]


def plan_week(pred: pd.DataFrame, staff: pd.DataFrame, store: pd.Series, week_start: pd.Timestamp,
              rules: Rules, uplift_pct: float = 0.0) -> dict:
    wk = week_slice(pred, week_start)
    o, c = int(store["open_hour"]), int(store["close_hour"])
    req = requirement(wk, rules, o, c, uplift_pct)
    rota = build_rota(req, staff[staff["store_id"] == store["store_id"]], rules, o, c)
    return {"week": wk, "rota": rota, "kpis": week_kpis(wk, rota, uplift_pct),
            "daily": daily_table(wk, rota, uplift_pct), "open_hour": o, "close_hour": c}


def stores(demand: pd.DataFrame) -> pd.DataFrame:
    return store_table(demand)
