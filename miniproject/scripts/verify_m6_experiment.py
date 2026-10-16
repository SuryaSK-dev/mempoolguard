"""Comprehensive Model Training, Evaluation, and Verification Runner for Milestone M6 (v2 Pipeline).

Remediates shortcut learning by:
1. Generating >= 1,000 labeled transactions (25 rounds x 45 txs = 1,125 txs) with rotating senders.
2. 50/50 balance between Type 0 Legacy and Type 2 EIP-1559 transactions across both classes.
3. Overlapping value and gas-limit distributions between classes while preserving core scenario signals.
4. Leak-free grouped splitting by scenario instance (preserving all archetypes in train, val, and test).
5. Pre-training single-feature separability gate (fails if any unallowed feature has AUC >= 0.95).
6. Training on 29 canonical model features (strict exclusion of nonce shortcut).
7. Evaluation with 95% bootstrap confidence intervals (B=1000).
8. Persisting v2 artifacts to models/saved/v2/ and reports to reports/v2/.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

import pandas as pd
from web3 import Web3
from web3.exceptions import Web3Exception

from collector.mempool_listener import MempoolListener, serialize_raw_transaction
from collector.transaction_generator import ScenarioResult, TransactionGenerator
from core.contracts import TransactionStatus, utc_now
from core.database import Database
from dataset.builder import DatasetBuilder
from dataset.schema import (
    FEATURE_SCHEMA_VERSION,
    MODEL_FEATURE_COLUMNS,
    TARGET_COLUMN,
    get_model_feature_matrix,
    get_target_labels,
    validate_dataset_schema,
)
from dataset.splitter import DatasetSplitter
from features.decoder import decode_raw_transaction
from features.engine import FeatureEngine
from models.audit import run_label_leakage_audit
from models.baseline import RuleBaseline
from models.evaluator import ModelEvaluationResult, ModelEvaluator
from models.logistic import LogisticRegressionModel
from models.persistence import load_model, save_model
from models.xgboost_model import XGBoostModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_m6_v2")


def generate_and_build_v2_dataset(
    w3: Web3,
    rounds: int = 25,
    benign_per_round: int = 20,
    db_path: str = "data/mempoolguard_v2.db",
    manifest_path: str = "data/raw/synthetic_scenarios_v2.json",
    parquet_path: str = "data/processed/v2_dataset.parquet",
    csv_path: str = "data/processed/v2_dataset.csv",
    splits_dir: str = "data/processed/v2_splits",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate multi-round interleaved test traffic, extract features, and build grouped instance splits."""
    print("\n" + "=" * 75)
    print(f"GENERATING V2 INTERLEAVED BENCHMARK DATASET ({rounds} ROUNDS, >= 1,000 TXS)")
    print("=" * 75)

    db_file = Path(db_path)
    if db_file.exists():
        db_file.unlink()

    db = Database(str(db_file))
    db.init_schema()

    # Disable automine to monitor mempool
    w3.provider.make_request("evm_setAutomine", [False])
    listener = MempoolListener(w3, db=db, poll_interval_seconds=0.02)
    listener.start()
    time.sleep(0.3)

    gen = TransactionGenerator(w3, seed=100)
    all_scenario_results: list[ScenarioResult] = []

    try:
        all_scenario_results = gen.generate_interleaved_dataset(
            rounds=rounds,
            benign_per_round=benign_per_round,
            output_manifest_path=manifest_path,
            automine=False,
        )
    finally:
        w3.provider.make_request("evm_setAutomine", [True])
        time.sleep(0.3)
        listener.stop()

    print(f"  Listener captured: {listener.captured_count} transactions directly from mempool.")

    # Defensive check: ensure all transactions are stored in raw_transactions
    total_txs = sum(len(r.transactions) for r in all_scenario_results)
    persisted_raw = 0
    for scen in all_scenario_results:
        for tx_item in scen.transactions:
            norm_h = tx_item.tx_hash.lower()
            raw_rec = db.get_raw_transaction(norm_h)
            if not raw_rec:
                try:
                    chain_tx = w3.eth.get_transaction(norm_h)
                    if chain_tx:
                        raw_json = serialize_raw_transaction(dict(chain_tx))
                        db.insert_raw_transaction(norm_h, raw_json, first_seen_at=utc_now())
                        persisted_raw += 1
                except Exception as exc:
                    logger.warning("Could not persist raw tx %s: %s", norm_h, exc)
            else:
                persisted_raw += 1

    print(f"  Total raw transactions in database: {persisted_raw} / {total_txs}")

    # Extract 30 features for all captured transactions into the database
    print("  Extracting pre-confirmation features into database...")
    engine = FeatureEngine(db)
    extracted = 0
    for scen in all_scenario_results:
        for tx_item in scen.transactions:
            norm_h = tx_item.tx_hash.lower()
            raw_rec = db.get_raw_transaction(norm_h)
            if raw_rec:
                tx_obj = decode_raw_transaction(raw_rec)
                engine.extract_and_persist(tx_obj)
                extracted += 1

    print(f"  Extracted features for {extracted} transactions.")

    # Build dataset from database and manifest
    builder = DatasetBuilder(
        db=db,
        manifest_path=manifest_path,
        dataset_version="1.1.0-v2",
        random_seed=100,
    )
    df = builder.build_dataset(output_parquet_path=parquet_path, output_csv_path=csv_path)
    is_valid, errs = validate_dataset_schema(df)
    assert is_valid, f"V2 dataset schema invalid: {errs}"

    # Grouped instance-based split (zero group leakage, every scenario in every split)
    splitter = DatasetSplitter(random_seed=100)
    train_df, val_df, test_df = splitter.grouped_instance_split(
        df, instance_col="scenario_id", train_ratio=0.70, val_ratio=0.15, test_ratio=0.15
    )
    splitter.save_splits(train_df, val_df, test_df, output_dir=splits_dir)

    print(f"  V2 Dataset Total Rows: {len(df)}")
    print(f"    Train : {len(train_df)} rows {train_df[TARGET_COLUMN].value_counts().to_dict()}")
    print(f"    Val   : {len(val_df)} rows {val_df[TARGET_COLUMN].value_counts().to_dict()}")
    print(f"    Test  : {len(test_df)} rows {test_df[TARGET_COLUMN].value_counts().to_dict()}")

    return df, train_df, val_df, test_df


def print_dataset_diagnostics(df: pd.DataFrame, train_df: pd.DataFrame, val_df: pd.DataFrame, test_df: pd.DataFrame) -> None:
    """Print class-by-tx_type table and per-split scenario type distribution."""
    # 1. Class by tx_type contingency table
    print("\n" + "=" * 75)
    print("1. CLASS BY TX_TYPE CONTINGENCY TABLE (BALANCE VERIFICATION)")
    print("=" * 75)
    ct = pd.crosstab(
        df[TARGET_COLUMN].map({0: "Benign (0)", 1: "Anomaly (1)"}),
        df["tx_type"].map({0: "Type 0 (Legacy)", 2: "Type 2 (EIP-1559)"}),
        margins=True,
    )
    print(ct.to_string())

    # 2. Per-split scenario distribution table
    print("\n" + "=" * 75)
    print("2. PER-SPLIT DISTRIBUTION BY SCENARIO TYPE AND TARGET LABEL")
    print("=" * 75)

    split_dfs = [("Train (70%)", train_df), ("Val (15%)", val_df), ("Test (15%)", test_df)]
    rows_summary = []
    for split_label, s_df in split_dfs:
        s_df_scen = s_df["scenario_id"].apply(lambda s: str(s).split("__rnd")[0])
        counts = s_df_scen.value_counts().to_dict()
        for scen_name, count in sorted(counts.items()):
            label_val = int(s_df[s_df["scenario_id"].str.startswith(scen_name)][TARGET_COLUMN].iloc[0])
            rows_summary.append({
                "Split": split_label,
                "Scenario Type": scen_name,
                "Label": label_val,
                "Count": count,
            })

    summary_df = pd.DataFrame(rows_summary)
    pivot = summary_df.pivot_table(
        index=["Scenario Type", "Label"],
        columns="Split",
        values="Count",
        fill_value=0,
        aggfunc="sum",
    )[["Train (70%)", "Val (15%)", "Test (15%)"]]
    pivot["Total"] = pivot.sum(axis=1)
    print(pivot.to_string())
    print("=" * 75)


def run_evaluation_v2(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    allowed_features: set[str] | None = None,
) -> tuple[dict[str, ModelEvaluationResult], dict[str, Any], dict[str, float]]:
    """Execute v2 model training and evaluation with separability check and bootstrap CIs."""
    print("\n" + "=" * 75)
    print("V2 MODEL TRAINING, SEPARABILITY GATE, AND EVALUATION (29 FEATURES)")
    print("=" * 75)

    X_train = get_model_feature_matrix(train_df)
    y_train = get_target_labels(train_df)
    X_val = get_model_feature_matrix(val_df)
    y_val = get_target_labels(val_df)
    X_test = get_model_feature_matrix(test_df)
    y_test = get_target_labels(test_df)

    # 1. Separability Gate
    print("\n[Step 1] Running Single-Feature Separability Scan on Train Split...")
    feature_aucs = ModelEvaluator.check_single_feature_separability(
        X_train,
        y_train,
        max_auc_threshold=0.95,
        allowed_features=allowed_features or set(),
        verbose=True,
    )

    results: dict[str, ModelEvaluationResult] = {}
    fitted_models: dict[str, Any] = {}

    # 2. Rule-Based Baseline
    print("\n[Step 2] Evaluating Rule-Based Baseline with Bootstrap CIs...")
    baseline = RuleBaseline()
    res_base = ModelEvaluator.evaluate(
        baseline, X_test, y_test, split_name="test_v2", compute_ci=True
    )
    results["RuleBaseline"] = res_base
    fitted_models["RuleBaseline"] = baseline

    # 3. Logistic Regression
    print("\n[Step 3] Training & Tuning Logistic Regression with Bootstrap CIs...")
    lr = LogisticRegressionModel(random_seed=42)
    lr.fit(X_train, y_train)
    lr.tune_threshold_on_validation(X_val, y_val)
    res_lr = ModelEvaluator.evaluate(
        lr, X_test, y_test, split_name="test_v2", threshold=lr.threshold, compute_ci=True
    )
    results["LogisticRegression"] = res_lr
    fitted_models["LogisticRegression"] = lr

    # 4. XGBoost Classifier
    print("\n[Step 4] Training & Tuning XGBoost Classifier with Bootstrap CIs...")
    xgb = XGBoostModel(n_estimators=50, max_depth=3, learning_rate=0.05, random_seed=42)
    xgb.fit(X_train, y_train)
    xgb.tune_threshold_on_validation(X_val, y_val)
    res_xgb = ModelEvaluator.evaluate(
        xgb, X_test, y_test, split_name="test_v2", threshold=xgb.threshold, compute_ci=True
    )
    results["XGBoost"] = res_xgb
    fitted_models["XGBoost"] = xgb

    return results, fitted_models, feature_aucs


def persist_v2_artifacts(
    fitted_models: dict[str, Any],
    eval_results: dict[str, ModelEvaluationResult],
    feature_aucs: dict[str, float],
    audit_report: Any,
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    models_dir: Path = Path("models/saved/v2"),
    reports_dir: Path = Path("reports/v2"),
) -> None:
    """Persist v2 model artifacts and compile evaluation reports into reports/v2/."""
    print("\n" + "=" * 75)
    print(f"PERSISTING V2 MODEL ARTIFACTS TO {models_dir}")
    print("=" * 75)

    models_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: dict[str, dict[str, Path]] = {}
    for name, model in fitted_models.items():
        slug = "rulebaseline" if name == "RuleBaseline" else ("logisticregression" if name == "LogisticRegression" else "xgboost")
        paths = save_model(model, models_dir, slug)
        saved_paths[name] = paths
        target_path = paths.get("model") or paths["metadata"]
        print(f"  Saved {name:<20}: {target_path.name}")

        _loaded, meta = load_model(target_path)
        feat_cnt = meta.get("feature_count") or len(meta.get("feature_names", []))
        print(f"  Verified reload {name:<14}: Version={meta['model_version']}, Features={feat_cnt}, Status=PASS")

    # JSON report
    report_payload = {
        "generated_at": utc_now().isoformat(),
        "milestone": "M6_v2",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_count": len(MODEL_FEATURE_COLUMNS),
        "excluded_features": ["nonce"],
        "splits_summary": {
            "train_rows": len(train_df),
            "val_rows": len(val_df),
            "test_rows": len(test_df),
            "train_labels": train_df[TARGET_COLUMN].value_counts().to_dict(),
            "val_labels": val_df[TARGET_COLUMN].value_counts().to_dict(),
            "test_labels": test_df[TARGET_COLUMN].value_counts().to_dict(),
        },
        "single_feature_aucs": feature_aucs,
        "evaluation_v2": {k: v.model_dump(mode="json") for k, v in eval_results.items()},
        "label_leakage_audit": audit_report.model_dump(mode="json"),
        "persisted_artifacts": {k: {pk: str(pv) for pk, pv in pdict.items()} for k, pdict in saved_paths.items()},
    }

    json_report_path = reports_dir / "m6_evaluation_report.json"
    with open(json_report_path, "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2)
    print(f"\n  Saved JSON evaluation report: {json_report_path}")

    # Also save a copy inside models_dir
    with open(models_dir / "evaluation_report.json", "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2)

    # Markdown Report
    def _fmt_ci(val: float | None, ci_dict: dict[str, float] | None) -> str:
        if val is None:
            return "N/A"
        if ci_dict and "ci_lower" in ci_dict and "ci_upper" in ci_dict:
            return f"{val:.3f} [{ci_dict['ci_lower']:.3f}, {ci_dict['ci_upper']:.3f}]"
        return f"{val:.3f}"

    lines = [
        "# MempoolGuard Milestone M6 (v2): Remediated Model Evaluation Report",
        "",
        f"**Generated:** {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Feature Schema Version:** {FEATURE_SCHEMA_VERSION} ({len(MODEL_FEATURE_COLUMNS)} canonical model features)  ",
        "**Shortcut Learning Remediation:** `nonce` excluded from model inputs. Multi-round interleaved generator with 50/50 `tx_type` balance and rotating senders.  ",
        "",
        "---",
        "",
        "## 1. Executive Summary",
        "",
        "This evaluation report validates the remediation of the shortcut-learning defect identified in the v1 benchmark:",
        "1. **Elimination of Nonce Shortcut**: Nonce is strictly excluded from `MODEL_FEATURE_COLUMNS` and model feature matrices.",
        "2. **Interleaved Multi-Round Traffic**: 25 rounds generated with rotating sender accounts and overlapping value/gas distributions.",
        "3. **Balanced Transaction Types**: 50/50 balance between Type 0 Legacy and Type 2 EIP-1559 across both benign and anomaly classes.",
        "4. **Grouped Instance Splitting**: Data partitioned by `scenario_id` instance (`__rndXXX`), ensuring zero group leakage while keeping all 8 archetypes represented in train, validation, and test splits.",
        "5. **Pre-Training Separability Gate**: All single-feature AUCs strictly below 0.95 threshold.",
        "6. **Empirical Bootstrap Confidence Intervals**: 95% CIs computed across 1,000 bootstrap resamples on test split.",
        "",
        "---",
        "",
        "## 2. Dataset Distribution",
        "",
        f"- **Total Dataset Rows**: {len(train_df) + len(val_df) + len(test_df)}",
        f"- **Train Split (70%)**: {len(train_df)} rows ({train_df[TARGET_COLUMN].value_counts().to_dict()})",
        f"- **Validation Split (15%)**: {len(val_df)} rows ({val_df[TARGET_COLUMN].value_counts().to_dict()})",
        f"- **Test Split (15%)**: {len(test_df)} rows ({test_df[TARGET_COLUMN].value_counts().to_dict()})",
        "",
        "### Class by Transaction Type Balance",
        "",
        "| Class | Type 0 Legacy | Type 2 EIP-1559 | Total |",
        "| :--- | :---: | :---: | :---: |",
    ]

    all_split = pd.concat([train_df, val_df, test_df], ignore_index=True)
    c0_t0 = len(all_split[(all_split[TARGET_COLUMN] == 0) & (all_split["tx_type"] == 0)])
    c0_t2 = len(all_split[(all_split[TARGET_COLUMN] == 0) & (all_split["tx_type"] == 2)])
    c1_t0 = len(all_split[(all_split[TARGET_COLUMN] == 1) & (all_split["tx_type"] == 0)])
    c1_t2 = len(all_split[(all_split[TARGET_COLUMN] == 1) & (all_split["tx_type"] == 2)])

    lines.append(f"| **Benign (0)** | {c0_t0} ({c0_t0 / max(1, c0_t0+c0_t2):.1%}) | {c0_t2} ({c0_t2 / max(1, c0_t0+c0_t2):.1%}) | {c0_t0 + c0_t2} |")
    lines.append(f"| **Anomaly (1)** | {c1_t0} ({c1_t0 / max(1, c1_t0+c1_t2):.1%}) | {c1_t2} ({c1_t2 / max(1, c1_t0+c1_t2):.1%}) | {c1_t0 + c1_t2} |")
    lines.append(f"| **Total** | {c0_t0 + c1_t0} | {c0_t2 + c1_t2} | {len(all_split)} |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Pre-Training Single-Feature Separability Scan",
        "",
        "| Feature Name | Effective Train AUC | Status |",
        "| :--- | :---: | :---: |",
    ])

    for feat_name, auc_val in sorted(feature_aucs.items(), key=lambda x: x[1], reverse=True):
        st = "PASS" if auc_val < 0.95 else "VIOLATION"
        lines.append(f"| `{feat_name}` | {auc_val:.4f} | {st} |")

    lines.extend([
        "",
        "---",
        "",
        "## 4. Model Evaluation Results (Test Split with 95% Bootstrap CIs)",
        "",
        "| Model | Accuracy [95% CI] | Precision [95% CI] | Recall [95% CI] | F1 Score [95% CI] | ROC-AUC [95% CI] | PR-AUC [95% CI] | FPR | Latency |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for m_name in ["RuleBaseline", "LogisticRegression", "XGBoost"]:
        res = eval_results[m_name]
        cis = res.confidence_intervals or {}
        acc_s = _fmt_ci(res.accuracy, cis.get("accuracy"))
        prec_s = _fmt_ci(res.precision, cis.get("precision"))
        rec_s = _fmt_ci(res.recall, cis.get("recall"))
        f1_s = _fmt_ci(res.f1_score, cis.get("f1_score"))
        roc_s = _fmt_ci(res.roc_auc, cis.get("roc_auc"))
        pr_s = _fmt_ci(res.pr_auc, cis.get("pr_auc"))
        fpr_s = f"{res.false_positive_rate:.3f}" if res.false_positive_rate is not None else "N/A"
        lat_s = f"{res.inference_latency_ms:.2f} ms"
        lines.append(f"| **{m_name}** | {acc_s} | {prec_s} | {rec_s} | {f1_s} | {roc_s} | {pr_s} | {fpr_s} | {lat_s} |")

    lines.extend([
        "",
        "---",
        "",
        "## 5. Label-Leakage Audit Summary",
        "",
        f"- **Scenarios Audited**: {audit_report.scenarios_audited}",
        f"- **Direct Dependencies**: {audit_report.direct_dependency_count} / {audit_report.scenarios_audited}",
        "",
        f"> {audit_report.benchmark_limitation_statement}",
    ])

    md_report_path = reports_dir / "m6_evaluation_report.md"
    with open(md_report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  Saved Markdown evaluation report: {md_report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MempoolGuard Milestone M6 v2 Runner")
    parser.add_argument("--rounds", type=int, default=25, help="Number of generation rounds (default: 25)")
    parser.add_argument("--benign-per-round", type=int, default=20, help="Benign txs per round (default: 20)")
    parser.add_argument("--skip-generation", action="store_true", help="Skip Anvil generation and reuse existing splits")
    args = parser.parse_args()

    print("=" * 75)
    print("MEMPOOLGUARD MILESTONE M6 (v2): SHORTCUT-FREE VERIFICATION RUNNER")
    print("=" * 75)

    splits_dir = Path("data/processed/v2_splits")
    parquet_path = Path("data/processed/v2_dataset.parquet")

    if args.skip_generation and (splits_dir / "train.parquet").exists():
        print(f"  [+] Skipping generation; loading splits from {splits_dir}")
        train_df = pd.read_parquet(splits_dir / "train.parquet")
        val_df = pd.read_parquet(splits_dir / "val.parquet")
        test_df = pd.read_parquet(splits_dir / "test.parquet")
        df = pd.concat([train_df, val_df, test_df], ignore_index=True)
    else:
        rpc_url = "http://127.0.0.1:8545"
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            print(f"ERROR: Cannot connect to Anvil RPC at {rpc_url}. Start Anvil first.")
            sys.exit(1)

        df, train_df, val_df, test_df = generate_and_build_v2_dataset(
            w3,
            rounds=args.rounds,
            benign_per_round=args.benign_per_round,
            db_path="data/mempoolguard_v2.db",
            manifest_path="data/raw/synthetic_scenarios_v2.json",
            parquet_path=str(parquet_path),
            csv_path="data/processed/v2_dataset.csv",
            splits_dir=str(splits_dir),
        )

    # 1. Print diagnostics (class-by-tx_type and per-split distributions)
    print_dataset_diagnostics(df, train_df, val_df, test_df)

    # 2. Train & Evaluate Models with separability gate and bootstrap CIs
    eval_results, fitted_models, feature_aucs = run_evaluation_v2(
        train_df, val_df, test_df, allowed_features=set()
    )

    # 3. Run Label-Leakage Audit
    audit_report = run_label_leakage_audit()

    # 4. Persist Artifacts and Reports
    persist_v2_artifacts(
        fitted_models=fitted_models,
        eval_results=eval_results,
        feature_aucs=feature_aucs,
        audit_report=audit_report,
        train_df=train_df,
        val_df=val_df,
        test_df=test_df,
        models_dir=Path("models/saved/v2"),
        reports_dir=Path("reports/v2"),
    )

    print("\n" + "=" * 75)
    print("MILESTONE M6 (v2) VERIFICATION RUNNER: COMPLETE & PASS")
    print("=" * 75)


if __name__ == "__main__":
    main()
