"""Main entry point for the MempoolGuard Streamlit Dashboard (Milestone M9)."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from core.config import load_config
from dashboard.api_client import ApiClient
from dashboard.components.analytics import render_analytics
from dashboard.components.audit import render_audit
from dashboard.components.decisions import render_decisions
from dashboard.components.models import render_models
from dashboard.components.overview import render_overview
from dashboard.components.risk import render_risk
from dashboard.components.transactions import render_transactions
from dashboard.components.tx_detail import render_tx_detail
from dashboard.data_loader import DataLoader


def main() -> None:
    """Run the MempoolGuard Streamlit application."""
    st.set_page_config(
        page_title="MempoolGuard Dashboard",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # 1. Load system configuration
    try:
        cfg = load_config()
        api_base_url = f"http://{cfg.api.host}:{cfg.api.port}"
        db_path = cfg.database.sqlite_path
    except (OSError, ValueError, KeyError) as exc:
        st.warning(f"Could not load config file, falling back to defaults: {exc}")
        api_base_url = "http://127.0.0.1:8000"
        db_path = "data/mempoolguard.db"

    # 2. Instantiate data loader and API client
    api_client = ApiClient(base_url=api_base_url)
    data_loader = DataLoader(db_path=db_path)

    # 3. Sidebar Configuration & Navigation
    with st.sidebar:
        st.title("🛡️ MempoolGuard")
        st.caption("Pre-Confirmation Risk Intelligence")

        st.markdown("---")

        # Live Health Snapshot in Sidebar
        health = api_client.check_health()
        api_online = health.get("is_connected", False)
        db_online = data_loader.is_available()

        st.markdown("**Subsystem Status**")
        c_api, c_db = st.columns(2)
        with c_api:
            st.write(f"API: {'🟢' if api_online else '🔴'}")
        with c_db:
            st.write(f"DB: {'🟢' if db_online else '🔴'}")

        st.markdown("---")

        # Navigation selector
        views = [
            "🌐 System Overview",
            "📋 Transaction Monitoring",
            "⚡ Risk Monitoring",
            "⚖️ Decision Monitoring",
            "📜 Audit Explorer",
            "🧠 Model Registry",
            "📊 Risk & Policy Analytics",
            "🔍 Transaction Inspector",
        ]
        selected_view = st.radio("Navigation", views, index=0)

        st.markdown("---")

        # Manual Refresh & Timestamp
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.rerun()

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        st.caption(f"Last updated:\n`{now_utc}`")

        st.markdown("---")
        st.info(
            "**Proof-of-Concept Notice**\n\n"
            "Decisions are application-level simulations. The dashboard does not alter consensus or block network transactions."
        )

    # 4. Prominent PoC Notice Banner
    st.warning(
        "⚠️ **MempoolGuard Local Proof-of-Concept**: All risk scores (0–100) and decisions (ALLOW / FLAG / HOLD) "
        "are simulated application-level risk evaluations on a local test environment. "
        "The system does NOT alter EVM consensus or physically reject transactions on the blockchain."
    )

    # 5. Route to Selected Component View
    if selected_view == "🌐 System Overview":
        render_overview(api_client, data_loader)
    elif selected_view == "📋 Transaction Monitoring":
        render_transactions(data_loader)
    elif selected_view == "⚡ Risk Monitoring":
        render_risk(data_loader)
    elif selected_view == "⚖️ Decision Monitoring":
        render_decisions(data_loader)
    elif selected_view == "📜 Audit Explorer":
        render_audit(data_loader)
    elif selected_view == "🧠 Model Registry":
        render_models()
    elif selected_view == "📊 Risk & Policy Analytics":
        render_analytics(data_loader)
    elif selected_view == "🔍 Transaction Inspector":
        render_tx_detail(data_loader)


if __name__ == "__main__":
    main()
