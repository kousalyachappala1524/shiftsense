"""Shared UI pieces: colours, CSS, badges, footer."""

from __future__ import annotations

from urllib.parse import quote

import pandas as pd
import streamlit as st

from engine.claude_brief import setting

APP = "ShiftSense"
INK, SLATE, LINE, COBALT, RED, AMBER, GREEN = "#111827", "#4B5563", "#E5E7EB", "#1D4ED8", "#B91C1C", "#B45309", "#059669"

CSS = f"""
<style>
  .block-container {{padding-top: 2.2rem; max-width: 1280px;}}
  h1, h2, h3 {{color: {INK}; letter-spacing: -0.01em;}}
  [data-testid="stMetric"] {{border: 1px solid {LINE}; border-radius: 10px; padding: 12px 14px; background: #fff;}}
  [data-testid="stMetricLabel"] p {{color: {SLATE}; font-size: 0.82rem;}}
  [data-testid="stMetricValue"] {{font-size: 1.45rem; font-variant-numeric: tabular-nums;}}
  .ss-badge {{display:inline-block; padding:2px 10px; border:1px solid {LINE}; border-radius:999px;
             font-size:0.78rem; color:{SLATE}; margin:0 6px 6px 0; background:#fff;}}
  .ss-note {{color:{SLATE}; font-size:0.85rem;}}
  .ss-hero {{border:1px solid {LINE}; border-radius:14px; padding:22px 24px; background:#fff;}}
  .ss-pill-red {{color:{RED}; font-weight:600;}}
</style>"""


def setup_page() -> None:
    st.set_page_config(page_title=f"{APP} | AI Workforce Planning Copilot", page_icon="📅", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)


def badges(items: list[str]) -> None:
    st.markdown(" ".join(f"<span class='ss-badge'>{b}</span>" for b in items if b), unsafe_allow_html=True)


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def waitlist_url() -> str | None:
    """WAITLIST_URL (a form) or CONTACT_EMAIL (mailto) from secrets/env, so no address lives in the code."""
    if setting("WAITLIST_URL"):
        return setting("WAITLIST_URL")
    email = setting("CONTACT_EMAIL")
    if not email:
        return None
    return ("mailto:" + email + "?subject=" + quote("ShiftSense Multi-site Pro pilot") + "&body=" +
            quote("Hi, I'd like to join the ShiftSense pilot. Company / number of sites: "))


def footer(mode: str) -> None:
    st.divider()
    if mode == "uk":
        c1, c2 = st.columns([3, 1])
        c1.markdown("**Running several sites?** Multi-site Pro (planned, £79/month) adds saved rota uploads, "
                    "trend reports and Deputy / Planday sync. Join the pilot waitlist and help shape it.")
        url = waitlist_url()
        if url:
            c2.link_button("Join the Multi-site Pro waitlist", url, type="secondary", width="stretch")
    st.markdown(
        "<span class='ss-note'><b>Disclaimer:</b> ShiftSense provides operational modelling and compliance checks "
        "based on published GOV.UK and Ministry of Labour & Employment rules. Confirm final payroll submissions and "
        "legal decisions with your payroll provider or employment adviser. Demo figures are synthetic. "
        "Built by Kousalya Chappala with Python, Streamlit and the Claude API.</span>", unsafe_allow_html=True)
