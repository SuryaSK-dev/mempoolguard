"""System Overview page component showing subsystem health and high-level KPIs."""

from __future__ import annotations

import streamlit as st

from dashboard.api_client import ApiClient
from dashboard.data_loader import DataLoader


def render_overview(api_client: ApiClient, data_loader: DataLoader) -> None:
    """Render the System Overview section with health indicators and key metrics."""
    st.header("🌐 System Overview")
    st.caption("Live operational status across all core MempoolGuard subsystems and data stores.")

    # 1. Subsystem Diagnostics
    health = api_client.check_health()
    db_available = data_loader.is_available()

    col1, col2, col3, col4 = st.columns(4)

    # FastAPI Status
    with col1:
        if health.get("is_connected"):
            st.success("**FastAPI Service**\n\n🟢 Connected (Port 8000)")
        else:
            st.error(f"**FastAPI Service**\n\n🔴 Offline\n\n`{health.get('error', 'Unreachable')}`")

    # Database Status
    with col2:
        if db_available:
            st.success("**SQLite Database**\n\n🟢 Connected (`data/mempoolguard.db`)")
        else:
            st.error("**SQLite Database**\n\n🔴 Offline or Unreadable")

    # EVM RPC Node Status
    with col3:
        rpc_info = health.get("rpc") or health.get("subsystems", {}).get("rpc", {})
        if rpc_info.get("status") == "HEALTHY":
            block_num = rpc_info.get("block_number", "N/A")
            st.success(f"**Anvil EVM Node**\n\n🟢 Connected (Block {block_num})")
        else:
            msg = rpc_info.get("message", "Offline / Degraded")
            st.warning(f"**Anvil EVM Node**\n\n🟡 Degraded\n\n`{msg}`")

    # M6 Model Registry
    with col4:
        models_info = health.get("models") or health.get("subsystems", {}).get("models", {})
        loaded_count = models_info.get("loaded_count", 0)
        if loaded_count > 0:
            st.success(f"**M6 Models Loaded**\n\n🟢 {loaded_count} Active\n\n`{', '.join(models_info.get('available_models', []))}`")
        else:
            st.warning("**M6 Models**\n\n🟡 None Active via API")

    st.markdown("---")

    # 2. Pipeline Volume KPI Cards
    st.subheader("📊 Pipeline Volume Metrics")
    counts = data_loader.get_summary_counts()

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    with kpi1:
        st.metric("Raw Transactions", f"{counts.get('raw_transactions', 0):,}")
    with kpi2:
        st.metric("Decoded Txs", f"{counts.get('decoded_transactions', 0):,}")
    with kpi3:
        st.metric("Feature Vectors", f"{counts.get('feature_vectors', 0):,}")
    with kpi4:
        st.metric("Total Decisions", f"{counts.get('decisions', 0):,}")
    with kpi5:
        st.metric("Audit Records", f"{counts.get('audit_records', 0):,}")

    st.markdown("---")

    # 3. Quick System Architecture Description
    with st.expander("ℹ️ About MempoolGuard Proof-of-Concept Architecture"):
        st.markdown(
            """
            **MempoolGuard** inspects unconfirmed pending cryptocurrency transactions in real-time,
            extracting 30 deterministic pre-confirmation features and applying trained machine learning models
            (Rule-Based Baseline, Logistic Regression, and XGBoost) to assign a risk score from **0 to 100**.

            Based on configurable risk thresholds:
            - **Score < 30.0** $\\to$ **`ALLOW`** (Benign low-risk transaction)
            - **30.0 $\\le$ Score $\\le$ 70.0** $\\to$ **`FLAG`** (Suspicious, requires elevated review)
            - **Score > 70.0** $\\to$ **`HOLD`** (High-risk anomaly / suspected attack pattern)

            > **Disclaimer**: All decisions are simulated at the application layer. The system does not alter
            > underlying blockchain consensus or physically halt node mempool propagation.
            """
        )
