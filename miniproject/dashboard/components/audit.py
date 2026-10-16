"""Audit Explorer component for inspecting immutable compliance and error records."""

from __future__ import annotations

import json

import streamlit as st

from dashboard.components.transactions import _format_hex
from dashboard.data_loader import DataLoader


def render_audit(data_loader: DataLoader) -> None:
    """Render the Audit Explorer section."""
    st.header("📜 Audit Trail Explorer")
    st.caption("Immutable append-only records tracking all risk evaluations, policy decisions, and pipeline errors.")

    df = data_loader.get_recent_audit_records(limit=100)

    if df.empty:
        st.info("No audit records found in the database.")
        return

    # Event type breakdown
    event_counts = data_loader.get_audit_event_distribution()
    ec1, ec2, ec3 = st.columns(3)
    with ec1:
        st.metric("Total Audit Events", f"{len(df):,}")
    with ec2:
        dec_cnt = event_counts.get("DECISION_ISSUED", 0)
        st.metric("✅ Decisions Issued", f"{dec_cnt:,}")
    with ec3:
        err_cnt = event_counts.get("PIPELINE_ERROR", 0)
        st.metric("⚠️ Pipeline / Fallback Errors", f"{err_cnt:,}")

    st.markdown("---")

    # Filters
    col_filter, col_search = st.columns([1, 2])
    with col_filter:
        event_types = ["ALL"] + sorted([str(e) for e in df["event_type"].unique()])
        selected_event = st.selectbox("Filter Event Type:", event_types)
    with col_search:
        search_tx = st.text_input("Filter by Tx Hash or Audit ID:", "").strip().lower()

    filtered_df = df.copy()
    if selected_event != "ALL":
        filtered_df = filtered_df[filtered_df["event_type"] == selected_event]

    if search_tx:
        filtered_df = filtered_df[
            filtered_df["tx_hash"].str.lower().str.contains(search_tx, na=False)
            | filtered_df["audit_id"].str.lower().str.contains(search_tx, na=False)
        ]

    st.write(f"Showing **{len(filtered_df)}** audit log records:")

    display_df = filtered_df.copy()
    display_df["display_hash"] = display_df["tx_hash"].apply(lambda h: _format_hex(h, 10, 8))
    display_df["display_id"] = display_df["audit_id"].apply(lambda i: _format_hex(i, 8, 6))

    st.dataframe(
        display_df[
            [
                "display_id",
                "display_hash",
                "event_type",
                "timestamp",
                "risk_score",
                "decision",
                "error_info",
            ]
        ].rename(
            columns={
                "display_id": "Audit ID",
                "display_hash": "Tx Hash",
                "event_type": "Event Type",
                "timestamp": "Timestamp (UTC)",
                "risk_score": "Risk Score",
                "decision": "Decision",
                "error_info": "Error / Fallback Reason",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    # Inspect single audit record JSON details
    st.markdown("---")
    st.subheader("🔍 Audit Record Inspector")
    record_ids = df["audit_id"].tolist()
    if record_ids:
        selected_id = st.selectbox("Select Audit ID to inspect complete details JSON:", record_ids)
        matched = df[df["audit_id"] == selected_id].iloc[0]
        st.write(f"**Transaction Hash**: `{matched['tx_hash']}`")
        st.write(f"**Event Type**: `{matched['event_type']}` | **Decision**: `{matched['decision']}` | **Risk Score**: `{matched['risk_score']}`")

        if matched["error_info"]:
            st.error(f"**Pipeline Error / Fallback Info**: {matched['error_info']}")

        raw_details = matched["details"]
        if isinstance(raw_details, str):
            try:
                details_json = json.loads(raw_details)
            except (json.JSONDecodeError, TypeError, ValueError):
                details_json = {"raw": raw_details}
        else:
            details_json = raw_details

        st.json(details_json)
