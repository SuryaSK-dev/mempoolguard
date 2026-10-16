"""Transaction Monitoring component showing persisted transactions and search."""

from __future__ import annotations

import streamlit as st

from dashboard.data_loader import DataLoader


def _format_hex(val: str | None, head: int = 8, tail: int = 6) -> str:
    """Safely format long hashes or addresses for compact display."""
    if not val:
        return "-"
    s = str(val).strip()
    if len(s) <= head + tail + 3:
        return s
    return f"{s[:head]}...{s[-tail:]}"


def render_transactions(data_loader: DataLoader) -> None:
    """Render the Transaction Monitoring section with tabular data and search."""
    st.header("📋 Transaction Monitoring")
    st.caption("Pre-confirmation transactions captured from the mempool and persisted in SQLite.")

    # Fetch real transactions
    df = data_loader.get_recent_transactions(limit=100)

    if df.empty:
        st.info("No persisted transactions found in the database. Run the transaction generator (M2) to produce traffic.")
        return

    # Filter controls
    col_search, col_status = st.columns([3, 1])
    with col_search:
        search_term = st.text_input("🔍 Search by Sender, Receiver, or Tx Hash:", "").strip().lower()
    with col_status:
        statuses = ["ALL"] + sorted([s for s in df["status"].dropna().unique()])
        selected_status = st.selectbox("Status Filter", statuses)

    # Apply filters
    filtered_df = df.copy()
    if selected_status != "ALL":
        filtered_df = filtered_df[filtered_df["status"] == selected_status]

    if search_term:
        filtered_df = filtered_df[
            filtered_df["tx_hash"].str.lower().str.contains(search_term, na=False)
            | filtered_df["sender"].str.lower().str.contains(search_term, na=False)
            | filtered_df["receiver"].str.lower().str.contains(search_term, na=False)
        ]

    st.write(f"Showing **{len(filtered_df)}** of **{len(df)}** loaded transactions:")

    # Formatted display dataframe
    display_df = filtered_df.copy()
    display_df["display_hash"] = display_df["tx_hash"].apply(lambda h: _format_hex(h, 10, 8))
    display_df["display_sender"] = display_df["sender"].apply(lambda a: _format_hex(a, 6, 4))
    display_df["display_receiver"] = display_df["receiver"].apply(lambda a: _format_hex(a, 6, 4))
    display_df["value_eth_formatted"] = display_df["value_eth"].apply(lambda v: f"{v:.4f} ETH")

    cols_to_show = [
        "display_hash",
        "first_seen_at",
        "display_sender",
        "display_receiver",
        "value_eth_formatted",
        "nonce",
        "gas_limit",
        "status",
        "block_number",
    ]

    st.dataframe(
        display_df[cols_to_show].rename(
            columns={
                "display_hash": "Tx Hash",
                "first_seen_at": "First Seen (UTC)",
                "display_sender": "Sender",
                "display_receiver": "Receiver",
                "value_eth_formatted": "Value",
                "nonce": "Nonce",
                "gas_limit": "Gas Limit",
                "status": "Status",
                "block_number": "Block",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    # Detailed hash copy helper
    with st.expander("🔎 Quick Copy Transaction Hash"):
        selected_hash = st.selectbox("Select Transaction Hash for full string copy:", df["tx_hash"].tolist())
        st.code(selected_hash, language="text")
