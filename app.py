"""ShiftSense: AI workforce planning copilot (Streamlit entry point).

This file is only the shell: page setup, the region toggle and the footer.
All maths lives in engine/ (tested with pytest); all screens live in ui/.
"""

import streamlit as st

from ui import common, india_view, uk_view

common.setup_page()

MODES = {"india": "India mode (2026 Labour Codes & state OT caps)",
         "uk": "UK mode (2026/27 NLW, 15% NI & WTR)"}

st.markdown(f"# {common.APP}")
initial = st.query_params.get("mode", "india")
mode = st.segmented_control("Region", list(MODES), format_func=MODES.get, key="mode",
                            default=initial if initial in MODES else "india", label_visibility="collapsed")
mode = mode or "india"
if st.query_params.get("mode") != mode:
    st.query_params["mode"] = mode

if mode == "uk":
    uk_view.render()
else:
    india_view.render()

common.footer(mode)
