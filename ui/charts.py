"""Plotly figures. Presentation only: every number comes from engine/."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from engine.demo_data import DAY_LABELS

from .common import AMBER, COBALT, INK, RED, SLATE


def gap_heatmap(req: np.ndarray, sch: np.ndarray, open_hour: int, height: int = 330) -> go.Figure:
    gap = sch - req
    hours = [f"{h:02d}:00" for h in range(open_hour, open_hour + req.shape[1])]
    text = [[f"{sch[d, h]}/{req[d, h]}" for h in range(req.shape[1])] for d in range(7)]
    zmax = max(4, int(np.abs(gap).max()))
    fig = go.Figure(go.Heatmap(
        z=gap, x=hours, y=DAY_LABELS, text=text, texttemplate="%{text}", textfont={"size": 11, "color": INK},
        zmin=-zmax, zmax=zmax, xgap=2, ygap=2,
        colorscale=[[0, RED], [0.42, "#FECACA"], [0.5, "#F9FAFB"], [0.58, "#DBEAFE"], [1, COBALT]],
        colorbar={"title": {"text": "staff vs need", "side": "right"}, "thickness": 10,
                  "tickvals": [-zmax, 0, zmax], "ticktext": ["short", "on target", "over"]},
        hovertemplate="%{y} %{x}<br>scheduled / required: %{text}<br>gap: %{z:+d}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), template="simple_white")
    return fig


def forecast_chart(history: pd.DataFrame, pred: pd.DataFrame, backtest_daily: pd.DataFrame,
                   events: pd.DataFrame, week_start: pd.Timestamp) -> go.Figure:
    hist = history.assign(date=lambda x: x["timestamp"].dt.normalize())
    hd = hist.groupby("date")["footfall"].sum()
    hd = hd[hd.index >= hd.index.max() - pd.Timedelta(weeks=10)]
    pdly = pred.groupby("date")["footfall"].sum()
    fig = go.Figure()
    fig.add_scatter(x=hd.index, y=hd.values, name="Actual footfall", line=dict(color=INK, width=1.6))
    fig.add_scatter(x=backtest_daily["date"], y=backtest_daily["footfall_fc"], name="Back-test forecast",
                    line=dict(color="#9CA3AF", width=1.4, dash="dot"))
    fig.add_scatter(x=pdly.index, y=pdly.values, name="Forecast", line=dict(color=COBALT, width=2.2, dash="dash"))
    lo, hi = hd.index.min(), pdly.index.max()
    for _, e in events[(events["end_date"] >= lo) & (events["start_date"] <= hi)].iterrows():
        minor = "salary" in e["event"].lower()  # monthly: shade lightly, no label (avoids clutter)
        label = {} if minor else dict(annotation_text=e["event"], annotation_position="top left",
                                      annotation_font_size=10, annotation_font_color=SLATE)
        fig.add_vrect(x0=e["start_date"], x1=e["end_date"] + pd.Timedelta(hours=23), fillcolor=COBALT,
                      opacity=0.03 if minor else 0.08, line_width=0, **label)
    fig.add_vrect(x0=week_start, x1=week_start + pd.Timedelta(days=7), fillcolor="#F59E0B", opacity=0.08, line_width=0)
    fig.update_layout(height=380, template="simple_white", margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.08), yaxis_title="Visitors per day")
    return fig


def hourly_heatmap(week: pd.DataFrame) -> go.Figure:
    hm = week.pivot_table(index="dow", columns="hour", values="footfall", aggfunc="sum").reindex(range(7))
    fig = go.Figure(go.Heatmap(z=hm.values, x=[f"{h:02d}:00" for h in hm.columns], y=DAY_LABELS,
                               colorscale=[[0, "#F9FAFB"], [1, COBALT]], xgap=2, ygap=2,
                               texttemplate="%{z:.0f}", textfont={"size": 10},
                               hovertemplate="%{y} %{x}: %{z:.0f} visitors<extra></extra>", colorbar={"thickness": 10}))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=320, template="simple_white", margin=dict(l=10, r=10, t=10, b=10))
    return fig


def india_timeline(sh: pd.DataFrame, open_hour: int, close_hour: int) -> go.Figure:
    fig = go.Figure(go.Bar(
        y=sh["name"] + " · " + sh["role"], x=sh["hours"], base=sh["start"], orientation="h",
        marker_color=[COBALT if kh else "#94A3B8" for kh in sh["key_holder"]],
        text=[f"{s:02d}:00–{e:02d}:00" for s, e in zip(sh["start"], sh["end"])], textposition="inside",
        insidetextfont={"color": "white", "size": 11}, hovertemplate="%{y}<br>%{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(range=[open_hour, close_hour], dtick=1, title="Hour of day")
    fig.update_layout(height=34 * len(sh) + 80, template="simple_white", margin=dict(l=10, r=10, t=10, b=10))
    return fig


def uk_timeline(day_shifts: pd.DataFrame, status: pd.Series) -> go.Figure:
    """Shifts for one day; colour by worst finding (red breach, amber warning, blue info, grey ok)."""
    colour = {"breach": RED, "warning": AMBER, "info": COBALT, "": "#94A3B8"}
    d0 = day_shifts["start"].dt.normalize()
    start_h = (day_shifts["start"] - d0).dt.total_seconds() / 3600
    end_h = (day_shifts["end"] - d0).dt.total_seconds() / 3600
    labels = day_shifts["employee_name"] + " · " + day_shifts["site"]
    fig = go.Figure(go.Bar(
        y=labels, x=end_h - start_h, base=start_h, orientation="h",
        marker_color=[colour.get(s, "#94A3B8") for s in status],
        text=[f"{a:%H:%M}–{b:%H:%M}" for a, b in zip(day_shifts["start"], day_shifts["end"])],
        textposition="inside", insidetextfont={"color": "white", "size": 11},
        hovertemplate="%{y}<br>%{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    lo = max(0, int(start_h.min()) - 1)
    hi = min(32, int(np.ceil(end_h.max())) + 1)
    fig.update_xaxes(range=[lo, hi], dtick=1, title="Hour of day (past 24 = next morning)")
    fig.update_layout(height=30 * len(day_shifts) + 90, template="simple_white", margin=dict(l=10, r=10, t=10, b=10))
    return fig


def site_cost_bars(sites: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    base = sites["gross_pay"] - sites["holiday_pay"]
    for name, vals, col in (("Pay (incl. overtime premium)", base, "#93C5FD"), ("Holiday accrual", sites["holiday_pay"], "#C4B5FD"),
                            ("Employer NI", sites["employer_ni"], COBALT)):
        fig.add_bar(name=name, x=sites["site"], y=vals, marker_color=col,
                    hovertemplate="%{x}<br>" + name + ": £%{y:,.2f}<extra></extra>")
    fig.update_layout(barmode="stack", height=300, template="simple_white", margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.12), yaxis_title="£ this week")
    return fig
