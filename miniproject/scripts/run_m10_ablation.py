"""Command-line entry point to execute the MempoolGuard Milestone M10 Ablation Study (v2 Pipeline).

Usage:
    python scripts/run_m10_ablation.py [--splits-dir data/processed/v2_splits] [--output-dir reports/v2]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from models.ablation import (
    FEATURE_GROUPS,
    AblationExperimentRunner,
    save_ablation_reports,
    validate_feature_groups,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("m10_ablation")


def main() -> int:
    """Run full ablation study on v2 dataset splits and optionally primary M5 dataset."""
    parser = argparse.ArgumentParser(description="MempoolGuard Milestone M10 Ablation Runner")
    parser.add_argument(
        "--splits-dir",
        type=str,
        default="data/processed/v2_splits",
        help="Path to splits directory (default: data/processed/v2_splits)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="reports/v2",
        help="Directory to save ablation reports (default: reports/v2)",
    )
    parser.add_argument(
        "--dataset-name",
        type=str,
        default="v2_Remediated_Benchmark",
        help="Name of primary benchmark dataset",
    )
    parser.add_argument(
        "--include-m5",
        action="store_true",
        default=False,
        help="Include primary M5 chronological dataset for comparison",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("MEMPOOLGUARD MILESTONE M10: CONTROLLED FEATURE GROUP ABLATION STUDY (v2)")
    print("=" * 80)

    # 1. Feature Groups Integrity Check
    print("\n[1/4] Validating canonical 29-feature group partitioning...")
    validate_feature_groups()
    total_group_feats = sum(len(g["features"]) for g in FEATURE_GROUPS.values())
    print(f"[+] Verified {len(FEATURE_GROUPS)} feature groups accounting for exactly {total_group_feats} model features:")
    for g_info in FEATURE_GROUPS.values():
        print(f"    - {g_info['id']}: {g_info['name']} ({len(g_info['features'])} features)")

    # 2. Load Primary Benchmark Splits (v2 or expanded)
    splits_path = Path(args.splits_dir)
    print(f"\n[2/4] Loading benchmark dataset splits from {splits_path}...")
    train_path = splits_path / "train.parquet"
    val_path = splits_path / "val.parquet"
    test_path = splits_path / "test.parquet"

    if not (train_path.exists() and val_path.exists() and test_path.exists()):
        # Fallback to expanded_splits if v2_splits not generated yet
        fallback = Path("data/processed/expanded_splits")
        if (fallback / "train.parquet").exists():
            print(f"  [!] {splits_path} not found, falling back to {fallback}")
            train_path = fallback / "train.parquet"
            val_path = fallback / "val.parquet"
            test_path = fallback / "test.parquet"
        else:
            print(f"[-] FAILED: Splits not found in {splits_path} or {fallback}")
            return 1

    train_df = pd.read_parquet(train_path)
    val_df = pd.read_parquet(val_path)
    test_df = pd.read_parquet(test_path)

    print(f"  Benchmark Train: {len(train_df)} samples ({dict(train_df['label'].value_counts())})")
    print(f"  Benchmark Val:   {len(val_df)} samples ({dict(val_df['label'].value_counts())})")
    print(f"  Benchmark Test:  {len(test_df)} samples ({dict(test_df['label'].value_counts())})")

    # 3. Optionally Load M5 Primary Splits
    all_results: list[dict[str, Any]] = []
    runner = AblationExperimentRunner(random_seed=42)

    if args.include_m5:
        m5_splits_dir = Path("data/processed/splits")
        if (m5_splits_dir / "train.parquet").exists():
            print(f"\n[Optional] Loading M5 chronological dataset splits ({m5_splits_dir})...")
            m5_train = pd.read_parquet(m5_splits_dir / "train.parquet")
            m5_val = pd.read_parquet(m5_splits_dir / "val.parquet")
            m5_test = pd.read_parquet(m5_splits_dir / "test.parquet")
            print("  -> Running ablations on M5 Chronological Dataset...")
            m5_results = runner.run_on_splits(m5_train, m5_val, m5_test, dataset_name="M5_Primary_Chronological")
            all_results.extend(m5_results)
            print(f"  [+] Completed {len(m5_results)} runs on M5 dataset.")

    # 4. Execute Ablation Experiment Runs on Primary Benchmark
    print(f"\n[3/4] Executing Leave-One-Group-Out Ablations on {args.dataset_name} (Seed 42)...")
    t0 = time.time()
    bench_results = runner.run_on_splits(train_df, val_df, test_df, dataset_name=args.dataset_name)
    all_results.extend(bench_results)
    elapsed = time.time() - t0
    print(f"[+] Completed {len(bench_results)} runs on {args.dataset_name} in {elapsed:.2f}s.")

    # Print summary table for benchmark results
    print("\n" + "=" * 90)
    print(f"ABLATION RESULTS: {args.dataset_name.upper()} (TEST SET: {len(test_df)} SAMPLES)")
    print("=" * 90)
    header = f"{'Model':<18} | {'Configuration':<30} | {'Feats':<5} | {'Prec':<7} | {'Recall':<7} | {'F1':<7} | {'PR-AUC':<7} | {'d_F1':<7}"
    print(header)
    print("-" * len(header))
    for r in bench_results:
        prec_s = f"{r['precision']:.3f}" if r["precision"] is not None else "N/A"
        rec_s = f"{r['recall']:.3f}" if r["recall"] is not None else "N/A"
        f1_s = f"{r['f1_score']:.3f}" if r["f1_score"] is not None else "N/A"
        pr_s = f"{r['pr_auc']:.3f}" if r["pr_auc"] is not None else "N/A"
        d_f1 = f"{r.get('delta_f1', 0.0):+.3f}" if r.get("delta_f1") is not None else "0.000"
        print(f"{r['model']:<18} | {r['config_name']:<30} | {r['feature_count']:<5} | {prec_s:<7} | {rec_s:<7} | {f1_s:<7} | {pr_s:<7} | {d_f1:<7}")

    # 5. Persist Output Artifacts
    print(f"\n[4/4] Persisting ablation reports to {args.output_dir}...")
    json_path, csv_path, md_path = save_ablation_reports(all_results, output_dir=args.output_dir)
    print(f"  [+] JSON Report:     {json_path}")
    print(f"  [+] CSV Report:      {csv_path}")
    print(f"  [+] Markdown Report: {md_path}")

    print("\n" + "=" * 80)
    print("MILESTONE M10 ABLATION STUDY EXPERIMENT COMPLETED SUCCESSFULLY!")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
