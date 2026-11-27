"""UK mode: check a rota before it's published (WTR, minimum wage, Employer NI, overtime)."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from config.uk_rates import RATE_TABLES, WTR
from engine import audit, ingestion, optimizer, uk_cost, uk_demo, wtr_rules
from engine import claude_brief as cb

from . import ai_panel, charts
from .common import badges

SEV_ICON = {"breach": "🔴 breach", "warning": "🟠 warning", "info": "🔵 info"}


def fmt_change(x: float) -> str:
    if not x:
        return "no cost"
    return f"saves £{-x:,.2f}/wk" if x < 0 else f"+£{x:,.2f}/wk"


# ------------------------------------------------------------------ cached steps

@st.cache_data(show_spinner=False)
def _read(file_hash: str, name: str, _data: bytes):
    return ingestion.read_table(_data, name)


@st.cache_data(show_spinner=False)
def _parse(file_hash: str, mapping_t: tuple, _df: pd.DataFrame):
    return ingestion.apply_mapping(_df, dict(mapping_t), "Europe/London")


@st.cache_data(show_spinner="Checking the rota...")
def _analyse(file_hash: str, mapping_t: tuple, week: str, rates_version: str, premium: float, holiday: bool,
             sales_t: tuple, _shifts: pd.DataFrame) -> dict:
    rates = RATE_TABLES[rates_version]
    wk = pd.Timestamp(week)
    s = _shifts[_shifts["start"].map(uk_cost.iso_week_start) == wk].reset_index(drop=True)
    emp = uk_cost.weekly_costs(s, rates, premium, holiday)
    sites = uk_cost.site_costs(s, emp, dict(sales_t))
    tot = uk_cost.totals(emp, sites)
    flags = wtr_rules.check_shifts(s)
    wins = optimizer.quick_wins(s, rates, premium)
    payload = cb.build_verified_payload(wk, rates, tot, sites, flags, emp, wins, premium)
    return {"shifts": s, "emp": emp, "sites": sites, "totals": tot, "flags": flags, "wins": wins, "payload": payload}


def _load_demo():
    data = uk_demo.DEMO_FILE.read_bytes()
    st.session_state["uk_src"] = {"bytes": data, "name": uk_demo.DEMO_FILE.name, "demo": True,
                                  "preset": "Planday export", "hash": hashlib.sha256(data).hexdigest()}


def _hero():
    st.markdown("### Check next week's UK rota before you publish it")
    st.markdown("ShiftSense reads a rota export from Deputy, Planday or a spreadsheet and shows, in seconds:")
    c = st.columns(4)
    c[0].markdown("**Working Time breaches**  \nclopenings across sites, missing breaks, under-18 rules, 48-hour weeks")
    c[1].markdown("**Minimum wage**  \n2026/27 NLW and age bands, with an April 2027 preview")
    c[2].markdown("**True labour cost**  \n15% Employer NI, 12.07% holiday accrual, overtime premiums")
    c[3].markdown("**Quick wins**  \nlegal shift moves that cut overtime and NI, with £ per week")
    st.write("")
    if st.button("Load 3-Site UK Retail Demo (see hidden NI & WTR traps in 5 seconds)", type="primary",
                 key="uk_demo_main", icon=":material/bolt:"):
        _load_demo()
        st.rerun()
    st.caption("Or upload your own rota in the sidebar (CSV or Excel, up to 5 MB / 5,000 shifts). "
               "Files are processed in memory for this session only.")


def render() -> None:
    st.session_state.setdefault("uk_src", None)
    with st.sidebar:
        st.markdown("## ShiftSense · UK")
        st.caption("Rota compliance and true labour cost, before the rota goes out.")
        if st.button("Load 3-site UK retail demo", type="primary", width="stretch", key="uk_demo_side"):
            _load_demo()
        up = st.file_uploader("Upload a rota (CSV or Excel)", type=["csv", "xlsx", "txt"], key="uk_upload")
        preset = st.selectbox("Export format", list(ingestion.PRESETS), index=2, key="uk_preset",
                              help="Pre-fills the column mapping. You can adjust it in the Data & mapping tab.")
        if up is not None:
            data = up.getvalue()
            h = hashlib.sha256(data).hexdigest()
            src = st.session_state["uk_src"]
            if not src or src["hash"] != h or src.get("preset") != preset:
                st.session_state["uk_src"] = {"bytes": data, "name": up.name, "demo": False, "preset": preset, "hash": h}
        st.divider()
        versions = list(RATE_TABLES)
        rv = st.selectbox("Rate table", versions, format_func=lambda v: RATE_TABLES[v].label, key="uk_rates")
        premium = st.slider("Overtime premium (company policy)", 1.0, 2.0, 1.5, 0.25, key="uk_premium",
                            help="UK law has no statutory overtime premium. Use 1.0 if hours above contract are "
                                 "paid at the normal rate.")
        holiday = st.toggle("12.07% holiday accrual for irregular-hours staff", True, key="uk_holiday")

    src = st.session_state["uk_src"]
    if not src:
        _hero()
        return

    # ---------------------------------------------------------- read + map + parse
    try:
        df, info = _read(src["hash"], src["name"], src["bytes"])
    except ingestion.IngestionError as e:
        st.error(str(e))
        return
    suggestion = ingestion.suggest_mapping(list(df.columns), src.get("preset", "Custom Excel/CSV"))
    mkey = f"uk_map_{src['hash'][:12]}_{src.get('preset')}"
    mapping = {f: st.session_state.get(f"{mkey}_{f}", suggestion[f]) for f in ingestion.CANONICAL}
    mapping = {f: (None if v in (None, "(not in file)") else v) for f, v in mapping.items()}
    missing = ingestion.missing_required(mapping)

    def mapping_editor(expanded: bool):
        with st.expander("Column mapping", expanded=expanded):
            st.caption("Match each ShiftSense field to a column in your file. Required fields are marked *.")
            opts = ["(not in file)"] + list(df.columns)
            cols = st.columns(3)
            for i, (f, (req, desc)) in enumerate(ingestion.CANONICAL.items()):
                cur = suggestion[f] if suggestion[f] in df.columns else "(not in file)"
                cols[i % 3].selectbox(f"{f}{' *' if req else ''}", opts, index=opts.index(cur), key=f"{mkey}_{f}", help=desc)

    if missing:
        st.error("Some required columns couldn't be matched: " + ", ".join(missing) + ". Map them below.")
        mapping_editor(True)
        return
    try:
        shifts_all, issues, warnings = _parse(src["hash"], tuple(sorted(mapping.items(), key=lambda x: x[0])), df)
    except ingestion.IngestionError as e:
        st.error(str(e))
        mapping_editor(True)
        return
    if shifts_all.empty:
        st.error("No valid shifts were found. Check the problems listed below and the column mapping.")
        st.dataframe(issues, hide_index=True, width="stretch")
        mapping_editor(True)
        return

    weeks = sorted(shifts_all["start"].map(uk_cost.iso_week_start).unique())
    sites = sorted(shifts_all["site"].unique())
    with st.sidebar:
        week = st.selectbox("Week", weeks, format_func=lambda w: f"w/c {pd.Timestamp(w):%d %b %Y}", key="uk_week") \
            if len(weeks) > 1 else weeks[0]
        with st.expander("Weekly sales by site (for labour cost %)", expanded=False):
            defaults = uk_demo.SITE_SALES if src["demo"] else {}
            sales = {s: st.number_input(s, 0.0, 10_000_000.0, float(defaults.get(s, 0.0)), 500.0,
                                        key=f"uk_sales_{src['hash'][:8]}_{s}") for s in sites}
    sales_t = tuple((k, v) for k, v in sales.items() if v)
    r = _analyse(src["hash"], tuple(sorted(mapping.items())), str(pd.Timestamp(week)), rv, premium, holiday,
                 sales_t, shifts_all)
    emp, site_tbl, tot, flags, wins, payload = r["emp"], r["sites"], r["totals"], r["flags"], r["wins"], r["payload"]
    st.session_state["uk_payload"] = payload
    rates = RATE_TABLES[rv]
    n_breach = int((flags["severity"] == "breach").sum()) if not flags.empty else 0
    n_warn = int((flags["severity"] == "warning").sum()) if not flags.empty else 0

    badges([f"{'Demo: fictional retailer “Loom & Lane UK”' if src['demo'] else src['name']}",
            f"w/c {pd.Timestamp(week):%d %b %Y}", f"{len(r['shifts'])} shifts", f"{tot['headcount']} staff",
            f"{len(sites)} site{'s' if len(sites) > 1 else ''}", f"Rates {rates.version}"])
    if rates.status != "in force":
        st.warning(f"Showing the {rates.label} rates. These apply from April 2027, not to rotas being worked today.")
    for w in warnings:
        st.warning(w)
    if len(issues):
        st.warning(f"{len(issues)} row(s) couldn't be read and were skipped. See the Data & mapping tab.")

    tabs = st.tabs(["Overview", "Compliance", "Cost & NI", "Quick wins", "Weekly brief & audit pack", "Data & mapping"])

    # ---------------------------------------------------------- overview
    with tabs[0]:
        c = st.columns(6)
        c[0].metric("Labour cost", f"£{tot['loaded_cost']:,.0f}", help="Loaded: pay + overtime premium + holiday accrual + Employer NI")
        c[1].metric("Labour % of sales", f"{tot['labour_cost_pct']:.1f}%" if tot["labour_cost_pct"] else "Add sales")
        c[2].metric("Employer NI", f"£{tot['employer_ni']:,.2f}")
        c[3].metric("Breaches", n_breach + tot["nmw_breaches"],
                    help="Working Time breaches plus minimum wage breaches")
        c[4].metric("Warnings", n_warn)
        c[5].metric("Quick wins", f"£{wins['weekly_saving']:,.2f}/wk", f"£{wins['monthly_saving']:,.0f} a month",
                    delta_color="off")
        left, right = st.columns([3, 2])
        with left:
            st.markdown("#### Cost by site this week")
            st.plotly_chart(charts.site_cost_bars(site_tbl), width="stretch", key="uk_chart_sites")
            site_show = site_tbl[["site", "headcount", "paid_hours", "gross_pay", "employer_ni", "loaded_cost", "sales",
                                  "labour_cost_pct"]]
            st.dataframe(site_show.style.format({
                "paid_hours": "{:,.1f}", "gross_pay": "£{:,.2f}", "employer_ni": "£{:,.2f}",
                "loaded_cost": "£{:,.2f}", "sales": "£{:,.0f}", "labour_cost_pct": "{:.1f}%"}, na_rep="–"),
                hide_index=True, width="stretch", column_config={
                "site": "Site", "headcount": "Staff", "paid_hours": "Paid hrs", "gross_pay": "Pay",
                "employer_ni": "Employer NI", "loaded_cost": "Loaded cost", "sales": "Sales",
                "labour_cost_pct": "Labour %"})
        with right:
            st.markdown("#### Fix before publishing")
            fixes = wins["compliance_fixes"]
            if not fixes:
                st.success("No breaches found. This rota can go out.")
            for f in fixes:
                st.markdown(f"- **{f['who']}**: {f['action']} ({fmt_change(f['cost_change_week'])})")
            if wins["cost_actions"]:
                st.markdown("#### Biggest saving")
                a = wins["cost_actions"][0]
                st.markdown(f"{a['detail']} **Saves £{a['saving_week']:.2f} a week.**")

    # ---------------------------------------------------------- compliance
    with tabs[1]:
        st.markdown("#### Working Time and minimum wage findings")
        if flags.empty and not tot["nmw_breaches"]:
            st.success("No Working Time or minimum wage issues found.")
        else:
            show = flags.copy()
            show["severity"] = show["severity"].map(SEV_ICON)
            st.dataframe(show[["severity", "employee_name", "site", "rule", "when", "detail", "rows"]], hide_index=True,
                         width="stretch", column_config={
                "severity": st.column_config.TextColumn("Severity", width="small"), "employee_name": "Employee",
                "site": "Site", "rule": "Rule", "when": "When",
                "detail": st.column_config.TextColumn("Detail", width="large"),
                "rows": st.column_config.TextColumn("File rows", width="small")})
        nmw = emp[emp["nmw_breach"]]
        if len(nmw):
            st.markdown("#### Minimum wage")
            st.dataframe(nmw[["employee_name", "age_band", "hourly_rate", "min_rate", "min_rate_name", "paid_hours",
                              "nmw_shortfall_per_hour"]], hide_index=True, width="stretch", column_config={
                "employee_name": "Employee", "age_band": "Age band",
                "hourly_rate": st.column_config.NumberColumn("Paid rate", format="£%.2f"),
                "min_rate": st.column_config.NumberColumn("Legal minimum", format="£%.2f"),
                "min_rate_name": "Rate", "paid_hours": "Hours",
                "nmw_shortfall_per_hour": st.column_config.NumberColumn("Shortfall / hr", format="£%.2f")})
        st.markdown("#### Rota by day")
        s = r["shifts"]
        days = sorted(s["start"].dt.normalize().unique())
        day = st.radio("Day", days, horizontal=True, format_func=lambda d: f"{pd.Timestamp(d):%a %d}", key="uk_day",
                       label_visibility="collapsed")
        day_sh = s[s["start"].dt.normalize() == day].sort_values(["site", "start"])
        worst = {}
        order = {"breach": 0, "warning": 1, "info": 2}
        for _, fl in flags.iterrows():
            for rr in str(fl["rows"]).replace(" ", "").split(","):
                if rr.isdigit():
                    cur = worst.get(int(rr))
                    if cur is None or order[fl["severity"]] < order[cur]:
                        worst[int(rr)] = fl["severity"]
        status = day_sh["row"].map(lambda x: worst.get(int(x), ""))
        st.plotly_chart(charts.uk_timeline(day_sh, status), width="stretch", key="uk_chart_day")
        st.caption("Red = breach, amber = warning, blue = night shift, grey = fine. Rest gaps are checked per person "
                   "across all sites, in real elapsed time.")

    # ---------------------------------------------------------- cost
    with tabs[2]:
        st.markdown("#### True cost per person this week")
        emp_show = emp[["employee_name", "sites", "age_band", "contract_type", "hourly_rate", "paid_hours",
                        "contracted_hours", "overtime_hours", "base_pay", "overtime_premium_pay", "holiday_pay",
                        "employer_ni", "loaded_cost", "loaded_hourly_cost", "nmw_breach"]].copy()
        emp_show["contract_type"] = emp_show["contract_type"].astype(str).str.replace("_", " ")
        # Blank contracted hours (irregular / zero-hours) show as a dash, not "None".
        emp_show["contracted_hours"] = emp_show["contracted_hours"].map(lambda v: "–" if pd.isna(v) else f"{v:g}")
        money_cols = ["hourly_rate", "base_pay", "overtime_premium_pay", "holiday_pay", "employer_ni", "loaded_cost",
                      "loaded_hourly_cost"]
        st.dataframe(emp_show.style.format({**{c: "£{:,.2f}" for c in money_cols},
                                            "paid_hours": "{:g}", "overtime_hours": "{:g}"}),
                     hide_index=True, width="stretch", column_config={
            "employee_name": "Employee", "sites": "Site(s)", "age_band": "Age",
            "contract_type": st.column_config.TextColumn("Contract", width="medium"),
            "hourly_rate": "Rate", "paid_hours": "Paid hrs",
            "contracted_hours": st.column_config.TextColumn("Contract hrs", alignment="right"),
            "overtime_hours": "OT hrs",
            "base_pay": "Base pay", "overtime_premium_pay": "OT premium", "holiday_pay": "Holiday 12.07%",
            "employer_ni": "Employer NI", "loaded_cost": "Loaded cost", "loaded_hourly_cost": "Loaded £/hr",
            "nmw_breach": st.column_config.CheckboxColumn("Below min wage")})
        c = st.columns(3)
        c[0].metric("Holiday accrual (irregular hours)", f"£{tot['holiday_pay']:,.2f}")
        c[1].metric("Overtime premium", f"£{tot['overtime_premium_pay']:,.2f}")
        c[2].metric("Day-one SSP exposure", f"£{tot['ssp_exposure_one_week_all_staff']:,.2f}",
                    help="If every employee were off sick for a week: the lower of £123.25 or 80% of weekly earnings each.")
        with st.expander("How the numbers are calculated"):
            st.markdown(
                f"- **Employer NI**: {rates.employer_ni_rate:.0%} of weekly pay above £{rates.ni_secondary_threshold_weekly:.0f}. "
                f"For under-21s and apprentices under 25 the threshold is £{rates.ni_upper_secondary_threshold_weekly:.0f}.\n"
                f"- **Minimum wage ({rates.label})**: 21+ £{rates.nlw_21_plus:.2f}, 18-20 £{rates.nmw_18_20:.2f}, "
                f"under 18 £{rates.nmw_16_17:.2f}, apprentice £{rates.nmw_apprentice:.2f}. Age comes from the date of "
                "birth on the shift date when available.\n"
                f"- **Holiday accrual**: {rates.holiday_accrual_irregular:.2%} of pay for irregular-hours (zero-hours) "
                "staff, shown as a separate line and included in NI-able pay.\n"
                "- **Overtime premium**: your company policy, applied to paid hours above contracted hours.\n"
                "- Employment Allowance and pension contributions are not included.")

    # ---------------------------------------------------------- quick wins
    with tabs[3]:
        c = st.columns(3)
        c[0].metric("Savings per week", f"£{wins['weekly_saving']:,.2f}")
        c[1].metric("Savings per month", f"£{wins['monthly_saving']:,.2f}")
        c[2].metric("Shift moves", len(wins["cost_actions"]))
        st.markdown("#### Cost moves (overtime → colleagues with spare hours)")
        if wins["cost_actions"]:
            st.dataframe(pd.DataFrame(wins["cost_actions"])[["site", "when", "from", "to", "hours", "saving_week",
                                                            "ni_saving_week"]],
                         hide_index=True, width="stretch", column_config={
                "site": "Site", "when": "Shift", "from": "From", "to": "To", "hours": "Hours",
                "saving_week": st.column_config.NumberColumn("Saving / wk", format="£%.2f"),
                "ni_saving_week": st.column_config.NumberColumn("of which NI", format="£%.2f")})
        else:
            st.info("No overtime shift can move without breaking a contract or Working Time rule this week.")
        st.markdown("#### Compliance fixes")
        if wins["compliance_fixes"]:
            fx = pd.DataFrame(wins["compliance_fixes"])
            fx["cost"] = fx["cost_change_week"].map(fmt_change)
            st.dataframe(fx[["type", "who", "when", "action", "cost"]], hide_index=True, width="stretch", column_config={
                "type": "Fix", "who": "Employee", "when": "Shift",
                "action": st.column_config.TextColumn("What to change", width="large"), "cost": "Cost effect"})
        else:
            st.success("Nothing to fix.")
        st.caption(wins["assumptions"] + " Every move is re-checked against Working Time rules and contracts, and "
                   "savings are recalculated for both people (pay, overtime premium, holiday accrual and NI).")

    # ---------------------------------------------------------- brief + audit
    with tabs[4]:
        ai_panel.status_banner()
        bkey = f"uk_brief_{cb.payload_hash(payload)[:16]}"
        if st.button("Generate weekly brief", type="primary", key="uk_gen"):
            text, srcname, err = ai_panel.generate("uk_brief", payload, cb.uk_brief, payload)
            if err:
                st.warning(err)
            st.session_state[bkey] = (srcname if text else "template", text or cb.uk_fallback_brief(payload))
        saved = cb.saved_example("uk_brief", payload) if not cb.get_api_key() else None
        default = ("saved", saved) if saved else ("template", cb.uk_fallback_brief(payload))
        brief_src, brief = st.session_state.get(bkey, default)
        with st.container(border=True):
            st.caption(ai_panel.caption_for(brief_src))
            st.markdown(brief)
        pack, meta = audit.build_pack(r["shifts"], emp, flags, brief, rates, src["bytes"], src["name"], brief_src, premium)
        c1, c2 = st.columns(2)
        c1.download_button("Download compliance audit pack (.zip)", pack,
                           f"shiftsense_audit_{pd.Timestamp(week):%Y%m%d}.zip", "application/zip", type="primary")
        c2.download_button("Download brief (.md)", brief.encode("utf-8"),
                           f"weekly_brief_{pd.Timestamp(week):%Y%m%d}.md", "text/markdown")
        st.caption(f"The pack holds every shift with its flags and loaded cost, this brief, and metadata (UTC timestamp, "
                   f"rate table {rates.version}, SHA-256 of your file: {meta['input_sha256'][:16]}…). "
                   f"Holiday records must be kept for {WTR.holiday_record_retention_years} years from April 2026; store "
                   "this pack alongside them.")
        with st.expander(f"What Claude sees (about {cb.approx_tokens(payload)} tokens; nothing else is sent)"):
            st.json(payload, expanded=False)

    # ---------------------------------------------------------- data
    with tabs[5]:
        c = st.columns(4)
        c[0].metric("File", info.file_type.upper())
        c[1].metric("Rows", info.rows)
        c[2].metric("Delimiter", {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(info.delimiter, "n/a"))
        c[3].metric("Encoding", info.encoding or "n/a")
        mapping_editor(False)
        st.markdown("#### Rows that couldn't be read")
        if len(issues):
            st.dataframe(issues, hide_index=True, width="stretch", column_config={"row": "File row"})
        else:
            st.success("Every row was read.")
        st.markdown("#### Parsed shifts")
        view = r["shifts"].copy()
        view["start"] = view["start"].dt.strftime("%a %d %b %H:%M")
        view["end"] = view["end"].dt.strftime("%a %d %b %H:%M")
        st.dataframe(view.drop(columns=["gender"]), hide_index=True, width="stretch")
        st.download_button("Download the demo rota file", uk_demo.DEMO_FILE.read_bytes(), uk_demo.DEMO_FILE.name,
                           "text/csv", help="A Planday-style semicolon CSV you can edit and re-upload.")
