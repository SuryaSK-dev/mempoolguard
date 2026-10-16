"""Model Monitoring component displaying trained M6 artifacts and evaluation metrics."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st


def render_models(saved_dir: str = "models/saved") -> None:
    """Render the M6 Model Monitoring section."""
    st.header("🧠 M6 Machine Learning Model Registry")
    st.caption("Active model artifacts, feature configurations, and validation/test performance.")

    base_path = Path(saved_dir)

    models_info = [
        {
            "id": "rule_baseline",
            "name": "RuleBasedBaseline",
            "type": "Deterministic Rule Baseline",
            "meta_file": "rulebaseline_metadata.json",
            "artifact_file": None,
        },
        {
            "id": "logistic_regression",
            "name": "LogisticRegression",
            "type": "Calibrated Linear Classifier",
            "meta_file": "logisticregression_metadata.json",
            "artifact_file": "logisticregression.joblib",
        },
        {
            "id": "xgboost",
            "name": "XGBoostClassifier",
            "type": "Gradient Boosted Trees (Primary)",
            "meta_file": "xgboost_metadata.json",
            "artifact_file": "xgboost.joblib",
        },
    ]

    cols = st.columns(3)

    for idx, m in enumerate(models_info):
        meta_path = base_path / m["meta_file"]
        has_meta = meta_path.exists()
        art_path = base_path / m["artifact_file"] if m["artifact_file"] else None
        has_art = art_path.exists() if art_path else True

        meta: dict = {}
        if has_meta:
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError, ValueError):
                meta = {}

        with cols[idx]:
            st.markdown(f"### {m['name']}")
            st.write(f"**Type**: {m['type']}")
            st.write(f"**Version**: `{meta.get('model_version', '1.0.0')}`")
            st.write(f"**Feature Schema**: `{meta.get('feature_version', '1.0.0')}` (30 canonical features)")

            if has_art and has_meta:
                st.success("🟢 Artifact Available & Ready")
            else:
                st.warning("🟡 Artifact Not Found on Disk")

            st.write(f"**Optimal Threshold**: `{meta.get('threshold', 0.5):.3f}`")

            if m["id"] == "xgboost":
                st.write(f"Trees (`n_estimators`): `{meta.get('n_estimators', 50)}`")
                st.write(f"Max Depth: `{meta.get('max_depth', 3)}`")
                st.write(f"Learning Rate: `{meta.get('learning_rate', 0.05)}`")
            elif m["id"] == "logistic_regression":
                st.write(f"Max Iterations: `{meta.get('max_iter', 1000)}`")
                st.write(f"C (Regularization): `{meta.get('C', 1.0)}`")

    st.markdown("---")

    # Evaluation report breakdown
    st.subheader("📈 M6 Benchmark Evaluation Summary")
    report_file = base_path / "evaluation_report.json"
    if report_file.exists():
        try:
            with open(report_file, encoding="utf-8") as f:
                report = json.load(f)

            gen_time = report.get("generated_at", "N/A")[:19]
            st.caption(f"Evaluation report generated: {gen_time} (Test Split Evaluation)")

            eval_splits = report.get("evaluation_a_m5_splits", {})
            rows = []
            for m_key, m_eval in eval_splits.items():
                rows.append(
                    {
                        "Model": m_key,
                        "Split": m_eval.get("split_name", "test"),
                        "Samples": m_eval.get("sample_count", 0),
                        "Accuracy": f"{m_eval.get('accuracy', 0.0):.4f}",
                        "F1-Score": f"{m_eval.get('f1_score', 0.0):.4f}",
                        "Precision": f"{m_eval.get('precision', 0.0):.4f}",
                        "Recall": f"{m_eval.get('recall', 0.0):.4f}",
                        "Inference Latency": f"{m_eval.get('inference_latency_ms', 0.0):.3f} ms",
                    }
                )

            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.info("Evaluation metrics table is empty.")

        except (OSError, json.JSONDecodeError, ValueError) as exc:
            st.warning(f"Could not parse evaluation report: {exc}")
    else:
        st.info("Evaluation report `models/saved/evaluation_report.json` not found.")
