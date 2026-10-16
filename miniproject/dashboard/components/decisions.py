"""Decision Monitoring component showing existing M7 policy decisions."""

from __future__ import annotations

import streamlit as st

from dashboard.components.transactions import _format_hex
from dashboard.data_loader import DataLoader


def _format_decision_badge(decision: str) -> str:
    """Format decision status with colored badge icons."""
    d = str(decision).upper()
    if d == "ALLOW":
        return "🟢 ALLOW"
    elif d == "FLAG":
        return "🟡 FLAG"
    elif d == "HOLD":
        return "🔴 HOLD"
    return d


def render_decisions(data_loader: DataLoader) -> None:
    """Render the Decision Monitoring section displaying existing M7 decisions."""
    st.header("⚖️ M7 Policy Decision Monitoring")
    st.caption("Application-level simulated decisions mapped deterministically from risk scores.")

    # High-level summary banner
    dist = data_loader.get_decision_distribution()
    total = sum(dist.values())

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Decisions", f"{total:,}")
    with c2:
        allow_cnt = dist.get("ALLOW", 0)
        pct = (allow_cnt / total * 100) if total > 0 else 0
        st.metric("🟢 ALLOW (Score < 30)", f"{allow_cnt:,}", f"{pct:.1f}%")
    with c3:
        flag_cnt = dist.get("FLAG", 0)
        pct = (flag_cnt / total * 100) if total > 0 else 0
        st.metric("🟡 FLAG (30 ≤ Score ≤ 70)", f"{flag_cnt:,}", f"{pct:.1f}%")
    with c4:
        hold_cnt = dist.get("HOLD", 0)
        pct = (hold_cnt / total * 100) if total > 0 else 0
        st.metric("🔴 HOLD (Score > 70)", f"{hold_cnt:,}", f"{pct:.1f}%")

    st.markdown("---")

    df = data_loader.get_recent_decisions(limit=100)
    if df.empty:
        st.info("No decision records found in the database. Run M7 verification or submit transactions via /decision endpoint.")
        return

    # Decision filter
    selected_filter = st.radio("Filter Decision Type:", ["ALL", "ALLOW", "FLAG", "HOLD"], horizontal=True)

    filtered_df = df.copy()
    if selected_filter != "ALL":
        filtered_df = filtered_df[filtered_df["decision"] == selected_filter]

    st.write(f"Showing **{len(filtered_df)}** decisions:")

    display_df = filtered_df.copy()
    display_df["display_hash"] = display_df["tx_hash"].apply(lambda h: _format_hex(h, 10, 8))
    display_df["decision_badge"] = display_df["decision"].apply(_format_decision_badge)
    display_df["simulated_badge"] = display_df["is_simulated"].apply(lambda s: "Simulated" if s else "Enforced")

    st.dataframe(
        display_df[
            [
                "id",
                "display_hash",
                "decision_badge",
                "risk_score",
                "reason_codes",
                "policy_version",
                "simulated_badge",
                "created_at",
            ]
        ].rename(
            columns={
                "id": "ID",
                "display_hash": "Tx Hash",
                "decision_badge": "Decision",
                "risk_score": "Risk Score",
                "reason_codes": "Reason Codes",
                "policy_version": "Policy",
                "simulated_badge": "Status",
                "created_at": "Timestamp (UTC)",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
