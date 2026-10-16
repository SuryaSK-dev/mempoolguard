"""Transaction Detail Inspector component providing deep drill-down for any transaction."""

from __future__ import annotations

import streamlit as st

from dashboard.components.decisions import _format_decision_badge
from dashboard.components.risk import _get_risk_badge
from dashboard.components.transactions import _format_hex
from dashboard.data_loader import DataLoader


def render_tx_detail(data_loader: DataLoader) -> None:
    """Render comprehensive Transaction Deep-Dive Inspector view."""
    st.header("🔍 Transaction Inspector & Profile")
    st.caption("Inspect the complete end-to-end journey of a single pre-confirmation transaction across features, ML inference, policy, and audit trail.")

    hashes = data_loader.get_all_transaction_hashes(limit=200)

    if not hashes:
        st.info("No transaction records found in the database.")
        return

    # Selection controls
    col_sel, col_text = st.columns([2, 3])
    with col_sel:
        selected_from_list = st.selectbox("Select Known Transaction:", hashes)
    with col_text:
        manual_hash = st.text_input("Or Enter / Paste Specific Tx Hash:", "").strip().lower()

    target_hash = manual_hash if manual_hash else selected_from_list

    st.markdown("---")

    detail = data_loader.get_transaction_detail(target_hash)
    if not detail:
        st.warning(f"Transaction `{target_hash}` not found in the database. Ensure the hash is correct.")
        return

    tx = detail["transaction"]
    fv = detail["features"]
    pred = detail["prediction"]
    dec = detail["decision"]
    audits = detail["audit_records"]

    # Top summary banner
    banner_c1, banner_c2, banner_c3 = st.columns([3, 1, 1])
    with banner_c1:
        st.subheader(f"Tx `{_format_hex(tx.tx_hash, 12, 10)}`")
        st.code(tx.tx_hash, language="text")
    with banner_c2:
        if dec:
            st.metric("Policy Decision", _format_decision_badge(dec.decision.value))
        else:
            st.metric("Policy Decision", "Pending Evaluation")
    with banner_c3:
        if pred:
            st.metric("M6 Risk Score", f"{pred.risk_score:.2f}", delta=_get_risk_badge(pred.risk_score), delta_color="off")
        elif audits and audits[0].risk_score is not None:
            st.metric("Audit Risk Score", f"{audits[0].risk_score:.2f}")
        else:
            st.metric("Risk Score", "N/A")

    st.markdown("---")

    # Structured Accordions / Tabs
    tab_tx, tab_feat, tab_ml, tab_dec, tab_audit = st.tabs(
        ["1. Transaction Metadata", "2. Feature Vector (30)", "3. Model Prediction", "4. Policy Decision", "5. Audit Trail"]
    )

    # 1. Transaction Metadata
    with tab_tx:
        st.markdown("#### Decoded Transaction Properties")
        val_eth = tx.value_wei / 1e18
        gas_gwei = (tx.gas_price_wei / 1e9) if tx.gas_price_wei else None
        calldata_bytes = max(0, (len(tx.input_data) - 2) // 2) if tx.input_data.startswith("0x") else len(tx.input_data) // 2

        tcol1, tcol2 = st.columns(2)
        with tcol1:
            st.write(f"**Sender**: `{tx.sender}`")
            st.write(f"**Receiver**: `{tx.receiver or 'Contract Creation'}`")
            st.write(f"**Value (ETH)**: `{val_eth:.6f} ETH`")
            st.write(f"**Value (Wei)**: `{tx.value_wei:,}`")
            st.write(f"**Nonce**: `{tx.nonce}`")
        with tcol2:
            st.write(f"**Gas Limit**: `{tx.gas_limit:,}`")
            st.write(f"**Gas Price (Gwei)**: `{gas_gwei:.2f}`" if gas_gwei else "**Gas Price**: N/A")
            st.write(f"**Status**: `{tx.transaction_status.value}`")
            st.write(f"**Block Number**: `{tx.block_number or 'Pending / None'}`")
            st.write(f"**First Seen At (UTC)**: `{tx.first_seen_at.isoformat()}`")

        st.write(f"**Calldata Size**: `{calldata_bytes} bytes`")
        if tx.input_data and tx.input_data != "0x":
            with st.expander("Calldata Payload (Hex)"):
                st.code(tx.input_data, language="text")

    # 2. Feature Vector
    with tab_feat:
        st.markdown("#### Pre-Confirmation Feature Extraction (Anti-Leakage Guaranteed)")
        if fv:
            st.write(f"**Feature Version**: `{fv.feature_version}` | **Extraction Timestamp**: `{fv.feature_timestamp.isoformat()}`")
            st.write(f"**Total Features**: `{len(fv.features)}`")

            # Table of features
            feat_rows = [{"Feature Name": k, "Value": f"{v:.6g}" if isinstance(v, float) else str(v)} for k, v in fv.features.items()]
            st.dataframe(feat_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No pre-calculated feature vector found for this transaction.")

    # 3. Model Prediction
    with tab_ml:
        st.markdown("#### M6 Machine Learning Inference")
        if pred:
            pcol1, pcol2 = st.columns(2)
            with pcol1:
                st.write(f"**Model Name**: `{pred.model_name}`")
                st.write(f"**Model Version**: `{pred.model_version}`")
                st.write(f"**Predicted Class**: `{'Anomaly (1)' if pred.predicted_class == 1 else 'Benign (0)'}`")
            with pcol2:
                st.write(f"**Risk Score (0–100)**: `{pred.risk_score:.2f}`")
                st.write(f"**Inference Latency**: `{pred.inference_latency_ms:.3f} ms`")
                st.write(f"**Prediction Time**: `{pred.prediction_timestamp.isoformat()}`")

            st.write(f"**Reason Codes**: `{', '.join(pred.reason_codes)}`")
            if pred.explanation:
                st.write(f"**Explanation**: {pred.explanation}")
        else:
            st.info("No direct model prediction record found for this transaction. (Risk score may be recorded in the audit log).")

    # 4. Policy Decision
    with tab_dec:
        st.markdown("#### M7 Policy Decision")
        if dec:
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                st.write(f"**Decision**: {_format_decision_badge(dec.decision.value)}")
                st.write(f"**Policy Version**: `{dec.policy_version}`")
                st.write(f"**Simulated**: `{dec.is_simulated}` (Proof-of-Concept)")
            with dcol2:
                st.write(f"**Created At**: `{dec.created_at.isoformat()}`")
                if dec.expires_at:
                    st.write(f"**Expires At**: `{dec.expires_at.isoformat()}`")

            st.write(f"**Reason Codes**: `{', '.join(dec.reason_codes)}`")
        else:
            st.info("No policy decision found for this transaction.")

    # 5. Audit Trail
    with tab_audit:
        st.markdown("#### Chronological Audit Records")
        if audits:
            for idx, a in enumerate(audits):
                with st.expander(f"Audit Event #{idx+1}: {a.event_type.value} at {a.timestamp.isoformat()}", expanded=True):
                    st.write(f"**Audit ID**: `{a.audit_id}`")
                    st.write(f"**Event Type**: `{a.event_type.value}` | **Decision**: `{a.decision.value if a.decision else 'None'}` | **Risk Score**: `{a.risk_score}`")
                    if a.error_info:
                        st.error(f"**Error Information**: {a.error_info}")
                    st.json(a.details)
        else:
            st.info("No audit records found for this transaction.")
