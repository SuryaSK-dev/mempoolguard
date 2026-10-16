"""Risk and Policy Analytics component with aggregate visualizations."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.data_loader import DataLoader


def render_analytics(data_loader: DataLoader) -> None:
    """Render the Risk and Policy Analytics section with charts and distribution metrics."""
    st.header("📊 Risk & Policy Analytics")
    st.caption("Visualizing empirical distributions of risk scores, policy decisions, and audit events.")

    # 1. Decision Distribution
    st.subheader("1. Policy Decision Distribution")
    dec_dist = data_loader.get_decision_distribution()
    total_dec = sum(dec_dist.values())

    if total_dec == 0:
        st.info("No decision records available to plot distribution.")
    else:
        df_dec = pd.DataFrame(
            [
                {"Decision": k, "Count": v, "Percentage": (v / total_dec) * 100}
                for k, v in dec_dist.items()
            ]
        )

        col_chart, col_table = st.columns([2, 1])
        with col_chart:
            # Altair bar chart with color coding
            color_scale = alt.Scale(
                domain=["ALLOW", "FLAG", "HOLD"],
                range=["#22c55e", "#eab308", "#ef4444"],
            )
            chart = (
                alt.Chart(df_dec)
                .mark_bar(cornerRadius=4)
                .encode(
                    x=alt.X("Decision:N", title="Policy Decision", sort=["ALLOW", "FLAG", "HOLD"]),
                    y=alt.Y("Count:Q", title="Number of Decisions"),
                    color=alt.Color("Decision:N", scale=color_scale, legend=None),
                    tooltip=["Decision", "Count", alt.Tooltip("Percentage:Q", format=".1f")],
                )
                .properties(height=280)
            )
            st.altair_chart(chart, use_container_width=True)

        with col_table:
            st.dataframe(
                df_dec.rename(columns={"Count": "Total Count", "Percentage": "Share (%)"}),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("---")

    # 2. Risk Score Distribution
    st.subheader("2. Empirical Risk Score Distribution (0–100)")
    scores = data_loader.get_risk_score_distribution()

    if not scores:
        st.info("No numerical risk scores found in audit records.")
    else:
        df_scores = pd.DataFrame({"Risk Score": scores})

        # Binned histogram using Altair
        hist_chart = (
            alt.Chart(df_scores)
            .mark_bar(color="#3b82f6", cornerRadius=3)
            .encode(
                x=alt.X("Risk Score:Q", bin=alt.Bin(maxbins=20, extent=[0, 100]), title="Risk Score Bucket (0–100)"),
                y=alt.Y("count()", title="Transaction Count"),
                tooltip=["count()"],
            )
            .properties(height=280)
        )
        st.altair_chart(hist_chart, use_container_width=True)

        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            st.metric("Total Scores Recorded", f"{len(scores):,}")
        with sc2:
            st.metric("Mean Risk Score", f"{df_scores['Risk Score'].mean():.2f}")
        with sc3:
            st.metric("Min Risk Score", f"{df_scores['Risk Score'].min():.2f}")
        with sc4:
            st.metric("Max Risk Score", f"{df_scores['Risk Score'].max():.2f}")

    st.markdown("---")

    # 3. Audit Event Breakdown
    st.subheader("3. Audit Event Classification")
    audit_dist = data_loader.get_audit_event_distribution()
    if not audit_dist:
        st.info("No audit event records available.")
    else:
        df_audit = pd.DataFrame(
            [{"Event": k, "Count": v} for k, v in audit_dist.items()]
        )
        audit_chart = (
            alt.Chart(df_audit)
            .mark_bar(color="#8b5cf6", cornerRadius=3)
            .encode(
                x=alt.X("Event:N", title="Event Type"),
                y=alt.Y("Count:Q", title="Event Count"),
                tooltip=["Event", "Count"],
            )
            .properties(height=240)
        )
        st.altair_chart(audit_chart, use_container_width=True)
