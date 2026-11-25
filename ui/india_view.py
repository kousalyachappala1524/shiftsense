"""India mode: forecast demand, build the rota, check Labour Codes working hours."""

from __future__ import annotations

import hashlib
import io

import pandas as pd
import streamlit as st

from config import india_rates as IR
from engine import claude_brief as cb
from engine import demo_data, india_rules
from engine.demo_data import DAY_LABELS
from engine.india_pipeline import HORIZON_DAYS, plan_week, store_forecast, stores, week_slice
from engine.kpis import inr
from engine.rota_builder import Rules

from . import ai_panel, charts
from .common import badges, csv_bytes

TAGLINE = "AI workforce planning copilot: forecast demand, build the rota, control labour cost."


@st.cache_data(show_spinner="Loading demo data for a fictional retailer...")
def load_demo() -> dict:
    return demo_data.demo_bundle()


@st.cache_data(show_spinner="Forecasting demand and back-testing accuracy...")
def cached_forecast(data_key: str, store_id: str, _demand: pd.DataFrame, _events: pd.DataFrame) -> dict:
    return store_forecast(_demand, _events, store_id)


@st.cache_data(show_spinner="Building the rota...")
def cached_plan(data_key: str, store_id: str, week: str, rules_t: tuple, uplift: float,
                _pred: pd.DataFrame, _staff: pd.DataFrame, _store: pd.Series) -> dict:
    return plan_week(_pred, _staff, _store, pd.Timestamp(week), Rules(*rules_t), uplift)


def week_label(ws: pd.Timestamp, pred: pd.DataFrame, i: int) -> str:
    wk = week_slice(pred, ws)
    evs = sorted({e for e in wk["event"].dropna().unique()})
    end = ws + pd.Timedelta(days=6)
    return f"Week {i + 1} · {ws:%d %b} – {end:%d %b %Y}" + (f" · {', '.join(evs)}" if evs else "")


def _sidebar():
    with st.sidebar:
        st.markdown("## ShiftSense · India")
        st.caption(TAGLINE)
        source = st.radio("Data", ["Demo data (fictional retailer)", "Upload my CSVs"], label_visibility="collapsed")
        demo = load_demo()
        bundle, data_key = demo, "demo"
        if source.startswith("Upload"):
            up_d = st.file_uploader("Hourly demand CSV", type="csv")
            up_s = st.file_uploader("Staff roster CSV", type="csv")
            up_e = st.file_uploader("Events CSV (optional)", type="csv")
            if not (up_d and up_s):
                st.info("Upload demand + staff files to plan with your own data. Showing demo data until then.")
            elif max(f.size for f in (up_d, up_s) + ((up_e,) if up_e else ())) > 5 * 1024 * 1024:
                st.error("Each file must be 5 MB or smaller. Showing demo data.")
            else:
                try:
                    raw = up_d.getvalue() + up_s.getvalue() + (up_e.getvalue() if up_e else b"")
                    bundle = {
                        "demand": demo_data.clean_demand(pd.read_csv(io.BytesIO(up_d.getvalue()))),
                        "staff": demo_data.clean_staff(pd.read_csv(io.BytesIO(up_s.getvalue()))),
                        "events": demo_data.clean_events(pd.read_csv(io.BytesIO(up_e.getvalue()))) if up_e
                        else demo["events"].iloc[0:0],
                    }
                    data_key = hashlib.sha256(raw).hexdigest()
                    st.success("Using your uploaded data.")
                except Exception as e:  # plain-English schema error instead of a traceback
                    st.error(f"Could not read the files: {e}")
                    bundle, data_key = demo, "demo"
        with st.expander("CSV templates (demo data)"):
            st.download_button("demand.csv", csv_bytes(demo["demand"]), "demand.csv", "text/csv")
            st.download_button("staff.csv", csv_bytes(demo["staff"]), "staff.csv", "text/csv")
            st.download_button("events.csv", csv_bytes(demo["events"]), "events.csv", "text/csv")

        store_tbl = stores(bundle["demand"])
        store_name = st.selectbox("Store", store_tbl["store_name"].tolist())
        store = store_tbl[store_tbl["store_name"] == store_name].iloc[0]
        fcst = cached_forecast(data_key, store["store_id"], bundle["demand"], bundle["events"])
        pred = fcst["forecast"]
        weeks = [fcst["start"] + pd.Timedelta(weeks=i) for i in range(HORIZON_DAYS // 7)]
        labels = [week_label(w, pred, i) for i, w in enumerate(weeks)]
        wi = st.selectbox("Week to plan", range(len(weeks)), format_func=lambda i: labels[i])

        with st.expander("Planning rules", expanded=False):
            d = Rules()
            rules = Rules(
                customers_per_staff_hour=st.slider("Customers per staff per hour", 5.0, 20.0, d.customers_per_staff_hour, 0.5,
                                                   help="Productivity standard: one person on the floor per this many visitors an hour."),
                min_staff=st.slider("Minimum staff on the floor", 1, 6, d.min_staff),
                min_shift_hours=st.slider("Shortest shift (hrs)", 3, 6, d.min_shift_hours),
                ft_min_shift_hours=st.slider("Shortest shift for full-timers (hrs)", 4, 9, d.ft_min_shift_hours,
                                             help="Full-time staff work full shifts; part-timers cover short peaks."),
                max_shift_hours=st.slider("Longest shift (hrs, incl. break)", 6, 12, d.max_shift_hours),
                min_rest_hours=st.slider("Minimum rest between shifts (hrs)", 8, 14, d.min_rest_hours),
                max_days_per_week=st.slider("Max working days per week", 4, 7, d.max_days_per_week),
                overtime_multiplier=st.slider("Overtime pay multiplier", 1.0, 2.5, d.overtime_multiplier, 0.25),
                weekly_hours_cap=st.slider("Weekly hours cap incl. overtime", 40, 60, d.weekly_hours_cap,
                                           help="Check the limits that apply in your state."),
            )
    return bundle, data_key, store_name, store, fcst, pred, weeks, labels, wi, rules


def render() -> None:
    bundle, data_key, store_name, store, fcst, pred, weeks, labels, wi, rules = _sidebar()
    week_start = weeks[wi]
    rules_t = tuple(rules.as_dict().values())
    plan = cached_plan(data_key, store["store_id"], str(week_start.date()), rules_t, 0.0, pred, bundle["staff"], store)
    k, rota = plan["kpis"], plan["rota"]

    st.markdown(f"<span class='ss-note'>{TAGLINE}</span>", unsafe_allow_html=True)
    b = [store_name, labels[wi].split(" · ", 1)[1], f"{k['headcount']} staff"]
    if data_key == "demo":
        b.append(f"Demo data · fictional retailer “{demo_data.BRAND}”")
    badges(b)

    tab_over, tab_fc, tab_rota, tab_kpi, tab_lc, tab_wi, tab_ai = st.tabs(
        ["Overview", "Forecast", "Rota", "KPIs", "Labour Codes", "What-if", "AI analyst"])

    with tab_over:
        c = st.columns(6)
        c[0].metric("Forecast sales", inr(k["forecast_sales_inr"]))
        c[1].metric("Labour cost %", f"{k['labour_cost_pct']:.1f}%")
        c[2].metric("Sales per labour hour", inr(k["sales_per_labour_hour_inr"]))
        c[3].metric("Coverage", f"{k['coverage_pct']:.1f}%")
        c[4].metric("Contract utilisation", f"{k['contract_utilisation_pct']:.0f}%")
        c[5].metric("Overtime hours", f"{k['overtime_hours']:.0f}")
        c = st.columns(6)
        c[0].metric("Forecast footfall", f"{k['forecast_footfall']:,.0f}")
        c[1].metric("Required hrs", f"{k['required_hours']:.0f}")
        c[2].metric("Scheduled hrs", f"{k['scheduled_hours']:.0f}")
        c[3].metric("Short hrs", f"{k['understaffed_hours']:.0f}")
        c[4].metric("Over hrs", f"{k['overstaffed_hours']:.0f}")
        c[5].metric("Labour cost", inr(k["labour_cost_inr"]))
        left, right = st.columns([3, 2])
        with left:
            st.markdown("#### Staff on shift vs staff needed")
            st.caption("Each cell shows scheduled / required people for that hour. Red = short, blue = over.")
            st.plotly_chart(charts.gap_heatmap(rota["required"], rota["scheduled"], plan["open_hour"]),
                            width="stretch", key="in_chart1")
        with right:
            st.markdown("#### What needs attention")
            notes = list(rota["alerts"])
            ot = rota["staff"].query("overtime_hours > 0").sort_values("overtime_hours", ascending=False)
            if len(ot):
                notes.append(f"Overtime: {', '.join(f'{r.name} ({r.overtime_hours:.0f}h)' for r in ot.head(3).itertuples())}")
            contract_total = rota["staff"]["contract_hours"].sum()
            if contract_total > k["required_hours"] * 1.05:
                notes.append(f"Capacity: the team is contracted for {contract_total:.0f} hours but demand needs "
                             f"{k['required_hours']:.0f}. The {contract_total - k['required_hours']:.0f}-hour surplus "
                             "shows up as over-covered quiet hours. Use it for replenishment, training or visual "
                             "merchandising, or review part-time contracts.")
            elif k["overstaffed_hours"] > 0.08 * k["scheduled_hours"]:
                notes.append(f"{k['overstaffed_hours']:.0f} staff-hours sit above need, mostly in quiet hours.")
            for n in notes or ["No gaps: every trading hour is covered."]:
                st.markdown(f"- {n}")
            with st.expander("How ShiftSense works"):
                st.markdown(
                    "1. **Forecast**: weekday × hour profile from the last 8 normal weeks, a recent-level adjustment, "
                    "and event uplifts learned from history (e.g. last year's Black Friday).\n"
                    "2. **Requirement**: visitors per hour ÷ productivity standard, with a minimum floor team.\n"
                    "3. **Rota**: key-holders open and close every day; the biggest gaps are filled first with the "
                    "best-fitting shift, within availability, weekly hours, rest and working-day rules. A final pass "
                    "moves or trims overtime where colleagues have spare contracted hours.\n"
                    "4. **Labour Codes**: daily and weekly hours, quarterly overtime caps and women's night-work "
                    "consent, by state.\n"
                    "5. **AI analyst**: Claude reads a compact summary of verified numbers and writes the report.")

    with tab_fc:
        bt = fcst["backtest"]
        c = st.columns(4)
        c[0].metric("Footfall error (daily MAPE)", f"{bt['mape_daily_footfall']:.1f}%")
        c[1].metric("Sales error (daily MAPE)", f"{bt['mape_daily_sales']:.1f}%")
        c[2].metric("Hourly error (WAPE)", f"{bt['wape_hourly_footfall']:.1f}%")
        c[3].metric("Recent trend vs 8 weeks", f"{(fcst['model']['level'] - 1) * 100:+.1f}%")
        st.caption(f"Accuracy is measured by hiding the last 4 weeks of actuals ({bt['start']:%d %b} – "
                   f"{bt['end']:%d %b %Y}), forecasting them, and comparing.")
        st.markdown("#### Daily footfall: last 10 weeks and next 4 weeks")
        st.caption("Shaded: retail events (light shading = salary days, 1st–3rd). Orange: the week being planned.")
        st.plotly_chart(charts.forecast_chart(fcst["history"], pred, bt["daily"], bundle["events"], week_start),
                        width="stretch", key="in_chart2")
        l, r = st.columns([3, 2])
        with l:
            st.markdown("#### Selected week: forecast visitors per hour")
            st.plotly_chart(charts.hourly_heatmap(plan["week"]), width="stretch", key="in_chart3")
        with r:
            st.markdown("#### Event uplifts learned from history")
            up = pd.DataFrame([{"Event": e, "Footfall": f"{v['footfall']:.2f}×", "Conversion": f"{v['conversion']:.2f}×",
                                "Sales / visitor": f"{v['sales_per_visitor']:.2f}×", "Days seen": v["days_seen"]}
                               for e, v in sorted(fcst["model"]["uplifts"].items())])
            st.dataframe(up, hide_index=True, width="stretch")
            st.caption("Each ratio compares event days with the same weekday in the 8 normal weeks before the event.")

    with tab_rota:
        st.markdown("#### Coverage heatmap")
        st.plotly_chart(charts.gap_heatmap(rota["required"], rota["scheduled"], plan["open_hour"]),
                        width="stretch", key="in_chart4")
        st.markdown("#### Weekly rota")
        rt = rota["staff"][["name", "role", "key_holder"] + DAY_LABELS + ["scheduled_hours", "contract_hours", "overtime_hours"]]
        st.dataframe(rt, hide_index=True, width="stretch", column_config={
            "name": "Name", "role": "Role", "key_holder": st.column_config.CheckboxColumn("Key-holder"),
            "scheduled_hours": st.column_config.NumberColumn("Hours", format="%d"),
            "contract_hours": st.column_config.NumberColumn("Contract", format="%d"),
            "overtime_hours": st.column_config.NumberColumn("Overtime", format="%d")})
        st.download_button("Download rota (CSV)", csv_bytes(rota["shifts"].drop(columns=["day_idx"])),
                           f"rota_{store['store_id']}_{week_start:%Y%m%d}.csv", "text/csv")
        st.markdown("#### Shift timeline")
        day = st.radio("Day", DAY_LABELS, horizontal=True, label_visibility="collapsed", key="in_day")
        sh = rota["shifts"][rota["shifts"]["day"] == day].sort_values(["start", "end"])
        if sh.empty:
            st.info("No shifts on this day.")
        else:
            st.plotly_chart(charts.india_timeline(sh, plan["open_hour"], plan["close_hour"]), width="stretch", key="in_chart5")
            st.caption("Blue = key-holder shifts (open/close responsibility). Grey = other team members.")

    with tab_kpi:
        st.markdown("#### Day by day")
        st.dataframe(plan["daily"], hide_index=True, width="stretch", column_config={
            "Sales (INR)": st.column_config.NumberColumn(format="₹%d"),
            "Labour cost (INR)": st.column_config.NumberColumn(format="₹%d"),
            "Labour cost %": st.column_config.NumberColumn(format="%.1f%%"),
            "Sales / labour hr": st.column_config.NumberColumn(format="₹%d")})
        st.markdown("#### People: hours, utilisation and overtime")
        ps = rota["staff"][["name", "role", "contract_hours", "scheduled_hours", "overtime_hours",
                            "utilisation_pct", "days_worked", "hourly_cost_inr", "labour_cost_inr"]]
        st.dataframe(ps, hide_index=True, width="stretch", column_config={
            "name": "Name", "role": "Role",
            "contract_hours": st.column_config.NumberColumn("Contract hrs", format="%d"),
            "scheduled_hours": st.column_config.NumberColumn("Scheduled hrs", format="%d"),
            "overtime_hours": st.column_config.NumberColumn("Overtime hrs", format="%d"),
            "utilisation_pct": st.column_config.ProgressColumn("Utilisation", format="%.0f%%", min_value=0, max_value=120),
            "days_worked": st.column_config.NumberColumn("Days", format="%d"),
            "hourly_cost_inr": st.column_config.NumberColumn("Cost / hr", format="₹%d"),
            "labour_cost_inr": st.column_config.NumberColumn("Week cost", format="₹%d")})
        with st.expander("KPI definitions"):
            st.markdown(
                "- **Labour cost % of sales**: scheduled hours × hourly cost (overtime at the multiplier) ÷ forecast sales.\n"
                "- **Sales per labour hour**: forecast sales ÷ scheduled staff-hours.\n"
                "- **Coverage**: required staff-hours that are actually scheduled ÷ all required staff-hours.\n"
                "- **Contract utilisation**: scheduled hours ÷ contracted hours across the team.\n"
                f"- **Schedule efficiency**: scheduled hours that meet demand ÷ all scheduled hours "
                f"(this week: {k['schedule_efficiency_pct']:.1f}%).\n"
                "- **Overtime**: hours beyond each person's contract.")

    # ---------------------------------------------------------------- Labour Codes
    with tab_lc:
        city = str(store_name).split(" - ")[0].strip()
        default_state = IR.CITY_STATE.get(city, "Central sphere")
        names = list(IR.PRESETS)
        c1, c2, c3 = st.columns([2, 1, 1])
        state = c1.selectbox("State rules", names, index=names.index(default_state), key=f"in_state_{store['store_id']}",
                             help="Defaults to the store's state. Values are editable planning defaults.")
        p = IR.PRESETS[state]
        daily = c2.number_input("Daily hours limit", 6.0, 12.0, float(p.daily_hours), 0.5, key=f"in_daily_{state}")
        cap = c3.number_input("Overtime cap / quarter (hrs)", 0.0, 300.0, float(p.ot_quarter_cap), 5.0, key=f"in_cap_{state}")
        st.caption(f"{p.note} The Labour Codes have been in force since 21 Nov 2025; several states were still on draft "
                   "rules in 2026, so check the current state notification.")
        preset = IR.IndiaPreset(state, daily, p.weekly_hours, cap, p.note)
        ish = india_rules.rota_to_shifts(rota["shifts"], bundle["staff"], week_start)
        findings, people, lc_tot = india_rules.check(ish, preset)
        st.session_state["in_lc_totals"] = lc_tot
        c = st.columns(5)
        c[0].metric("Breaches", lc_tot.get("breaches", 0))
        c[1].metric("Warnings (overtime)", lc_tot.get("warnings", 0))
        c[2].metric("Women's late shifts", lc_tot.get("women_night_shift_reminders", 0),
                    f"{lc_tot.get('women_needing_consent', 0)} women need consent on file", delta_color="off",
                    help="Work after 19:00 or before 06:00 needs her written consent and safe transport arrangements.")
        c[3].metric("Overtime hours", f"{lc_tot.get('overtime_hours', 0):.1f}")
        c[4].metric("Overtime pay (2×)", inr(lc_tot.get("overtime_pay_inr", 0)))
        st.markdown("#### Findings")
        if findings.empty:
            st.success("No Labour Codes issues in this rota.")
        else:
            st.dataframe(findings.drop(columns=["shifts"]), hide_index=True, width="stretch", column_config={
                "name": "Employee", "role": "Role", "rule": "Rule", "severity": "Severity", "when": "When",
                "detail": st.column_config.TextColumn("Detail", width="large")})
        with st.expander("Per person: working hours and overtime"):
            st.dataframe(people, hide_index=True, width="stretch", column_config={
                "name": "Employee", "role": "Role", "days": "Days", "working_hours": "Working hrs",
                "overtime_hours": "Overtime hrs", "quarter_ot_if_repeated": "Quarter OT if repeated",
                "overtime_pay_inr": st.column_config.NumberColumn("Overtime pay", format="₹%d"),
                "night_shifts_women": "Late shifts (women)"})
        st.caption("Working hours = shift length minus a 1-hour rest interval on shifts over 5 hours. Overtime is paid "
                   "at twice the ordinary rate (the planner's hourly cost is used as the rate).")

    with tab_wi:
        st.markdown("#### What if footfall changes?")
        st.caption("Move the slider to stress-test the plan: a campaign, a competitor opening, a heatwave. "
                   "Staffing and the rota are rebuilt on the fly.")
        uplift = st.slider("Footfall change vs forecast", -30, 50, 20, 5, format="%d%%", key="in_uplift")
        scen = cached_plan(data_key, store["store_id"], str(week_start.date()), rules_t, float(uplift),
                           pred, bundle["staff"], store)
        sk = scen["kpis"]
        c = st.columns(6)
        c[0].metric("Required hrs", f"{sk['required_hours']:.0f}", f"{sk['required_hours'] - k['required_hours']:+.0f}", delta_color="off")
        c[1].metric("Scheduled hrs", f"{sk['scheduled_hours']:.0f}", f"{sk['scheduled_hours'] - k['scheduled_hours']:+.0f}", delta_color="off")
        c[2].metric("Coverage", f"{sk['coverage_pct']:.1f}%", f"{sk['coverage_pct'] - k['coverage_pct']:+.1f} pts")
        c[3].metric("Overtime hours", f"{sk['overtime_hours']:.0f}", f"{sk['overtime_hours'] - k['overtime_hours']:+.0f}",
                    delta_color="inverse")
        c[4].metric("Labour cost", inr(sk["labour_cost_inr"]), inr(sk["labour_cost_inr"] - k["labour_cost_inr"]),
                    delta_color="inverse")
        c[5].metric("Labour cost %", f"{sk['labour_cost_pct']:.1f}%", f"{sk['labour_cost_pct'] - k['labour_cost_pct']:+.1f} pts",
                    delta_color="inverse")
        short_days = scen["daily"].query("`Short hrs` > 0").sort_values("Short hrs", ascending=False)
        if uplift == 0:
            rec = "This is the base plan."
        elif sk["understaffed_hours"] > k["understaffed_hours"] + 5:
            days = ", ".join(f"{r['Day']} ({r['Short hrs']}h)" for _, r in short_days.head(3).iterrows())
            rec = (f"The current team can't absorb this. Book about **{sk['understaffed_hours']:.0f} extra staff-hours** "
                   f"of part-time or agency cover, mainly {days}, or accept slower service at peaks.")
        elif uplift < 0 and sk["overstaffed_hours"] > k["overstaffed_hours"]:
            rec = (f"Demand falls but contracted hours stay. Expect **{sk['overstaffed_hours']:.0f} idle staff-hours**: "
                   "plan training, stock counts or visual merchandising into quiet hours, and pause overtime.")
        else:
            rec = "The team absorbs this change within contracts and overtime limits."
        st.markdown(f"**Recommendation:** {rec}")
        st.plotly_chart(charts.gap_heatmap(scen["rota"]["required"], scen["rota"]["scheduled"], scen["open_hour"]),
                        width="stretch", key="in_chart6")
        scenario = {"footfall_change_pct": uplift, "required_hours": sk["required_hours"],
                    "coverage_pct": round(sk["coverage_pct"], 1), "overtime_hours": sk["overtime_hours"],
                    "labour_cost_pct": round(sk["labour_cost_pct"], 1), "recommendation": rec} if uplift else None

    with tab_ai:
        ctx = cb.build_india_payload(store_name, labels[wi], plan, fcst, rules.as_dict(),
                                     st.session_state.get("in_lc_totals"), scenario)
        ctx_key = f"{data_key}|{store['store_id']}|{week_start.date()}|{rules_t}"
        if st.session_state.get("in_ctx_key") != ctx_key:
            st.session_state.update(in_ctx_key=ctx_key, in_chat=[], in_report=None)
        has_key = ai_panel.status_banner()
        rep_col, chat_col = st.columns(2, gap="large")
        with rep_col:
            st.markdown("#### Weekly management report")
            st.caption("One click: demand outlook, coverage, cost, Labour Codes and three actions for the store manager.")
            if st.button("Generate report", type="primary", key="in_gen"):
                text, src, err = ai_panel.generate("india_report", ctx, cb.india_report, ctx)
                if err:
                    st.warning(err)
                st.session_state["in_report"] = (src, text or cb.india_offline_report(ctx))
            if st.session_state.get("in_report"):
                src, text = st.session_state["in_report"]
                with st.container(border=True):
                    st.markdown(text)
                st.download_button("Download report (.md)", text.encode("utf-8"),
                                   f"weekly_report_{store['store_id']}_{week_start:%Y%m%d}.md", "text/markdown")
        with chat_col:
            st.markdown("#### Ask your workforce data")
            st.caption("Questions are answered from this store-week's verified numbers only.")
            suggestions = ["Why are we short-staffed, and when?",
                           "Where can we cut labour cost without hurting coverage?",
                           "Which shifts need women's night-work consent?"]
            picked = None
            for i, s in enumerate(suggestions):
                if st.button(s, key=f"in_sugg{i}", disabled=not has_key, type="tertiary", icon=":material/chat:"):
                    picked = s
            box = st.container(height=360, border=True)
            if not st.session_state["in_chat"] and not has_key:
                box.caption("Once an API key is set, ask things like “Which hours on Saturday are short, and who could "
                            "cover them?”. Claude answers from the plan data only.")
            for m in st.session_state["in_chat"]:
                box.chat_message(m["role"]).markdown(m["content"])
            q = st.chat_input("Ask about this week's plan..." if has_key else "Add an API key to ask questions",
                              disabled=not has_key, key="in_chat_input")
            q = q or picked
            if q and has_key:
                box.chat_message("user").markdown(q)
                hist = st.session_state["in_chat"]
                text, src, err = ai_panel.generate("india_ask", ctx, cb.india_ask, ctx, hist, q,
                                                   extra=q + "|" + str(len(hist)))
                if err:
                    box.warning(err)
                if text:
                    box.chat_message("assistant").markdown(text)
                    st.session_state["in_chat"] += [{"role": "user", "content": q}, {"role": "assistant", "content": text}]
        with st.expander(f"What Claude sees (about {cb.approx_tokens(ctx)} tokens of verified numbers)"):
            st.json(ctx, expanded=False)
