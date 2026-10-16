"""Standalone execution script for Milestone M11: Latency and End-to-End Validation.

Executes the full benchmark pipeline, records empirical latencies,
validates reliability and idempotency, and outputs structured reports.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from benchmark.runner import M11ValidationRunner, save_validation_reports


def main() -> int:
    t_start = time.time()
    print("=" * 80)
    print("MEMPOOLGUARD MILESTONE M11: LATENCY & END-TO-END VALIDATION SUITE")
    print("=" * 80)

    runner = M11ValidationRunner()
    try:
        return _run_benchmarks(runner, t_start)
    finally:
        runner.cleanup()


def _run_benchmarks(runner: M11ValidationRunner, t_start: float) -> int:

    print("\n[1/8] Inspecting Local Runtime Environment...")
    env = runner.inspect_environment()
    print(f"  OS               : {env['os']}")
    print(f"  CPU              : {env['cpu_model']} ({env['logical_cores']} cores)")
    print(f"  RAM              : {env['free_ram_gb']} GB Free / {env['total_ram_gb']} GB Total")
    print(f"  Python           : {env['python']}")
    print(f"  FastAPI / Uvicorn: {env['fastapi']} / {env['uvicorn']}")
    print(f"  ML Frameworks    : Scikit-Learn {env['scikit_learn']} | XGBoost {env['xgboost']}")
    print(f"  SQLite Version   : {env['sqlite']} (WAL mode)")
    print(f"  Foundry Anvil    : {env['anvil_version']} (Online: {env['anvil_online']})")

    print("\n[2/8] Executing Live End-to-End Pipeline & Trace Generation...")
    trace = runner.validate_end_to_end_trace()
    print(f"  Target Tx Hash   : {trace['tx_hash']}")
    print(f"  Model Evaluated  : {trace['model_name']} v{trace['model_version']}")
    print(f"  Risk Score       : {trace['risk_score']}")
    print(f"  Decision Issued  : {trace['decision']} (Simulated={trace['is_simulated']})")
    print(f"  Reason Codes     : {trace['reason_codes']}")
    print(f"  Audit ID         : {trace['audit_id']}")
    print(f"  Total E2E Pipeline Latency: {trace['stage_durations_ms']['total_end_to_end_ms']} ms")
    print("  Stage Breakdown:")
    for stage, dur in trace['stage_durations_ms'].items():
        print(f"    - {stage:<28}: {dur:.4f} ms")

    print("\n[3/8] Measuring Component Latencies (N=25 repeated samples)...")
    comp_latencies = runner.benchmark_component_latencies(sample_count=25)
    print(f"{'Component':<30} {'Samples':>7} {'Min(ms)':>9} {'Median(ms)':>11} {'Mean(ms)':>10} {'P95(ms)':>9} {'Max(ms)':>9}")
    print("-" * 90)
    for comp, stats in comp_latencies.items():
        p95_str = f"{stats['p95_ms']:.4f}" if isinstance(stats['p95_ms'], (int, float)) else str(stats['p95_ms'])
        print(f"{comp:<30} {stats['sample_count']:>7} {stats['min_ms']:>9.4f} {stats['median_ms']:>11.4f} {stats['mean_ms']:>10.4f} {p95_str:>9} {stats['max_ms']:>9.4f}")

    print("\n[4/8] Benchmarking M6 Model Inference Latencies (N=50 samples)...")
    model_latencies = runner.benchmark_model_latencies(sample_count=50)
    print(f"{'Model':<25} {'Samples':>7} {'Min(ms)':>9} {'Median(ms)':>11} {'Mean(ms)':>10} {'P95(ms)':>9} {'Max(ms)':>9}")
    print("-" * 85)
    for model_name, stats in model_latencies.items():
        p95_str = f"{stats['p95_ms']:.4f}" if isinstance(stats['p95_ms'], (int, float)) else str(stats['p95_ms'])
        print(f"{model_name:<25} {stats['sample_count']:>7} {stats['min_ms']:>9.4f} {stats['median_ms']:>11.4f} {stats['mean_ms']:>10.4f} {p95_str:>9} {stats['max_ms']:>9.4f}")

    print("\n[5/8] Benchmarking Live FastAPI Service & Concurrency (HTTP over Uvicorn)...")
    api_latencies, e2e_api_summary, concurrency_results = runner.benchmark_api_and_concurrency(sample_count=25)
    print(f"{'Endpoint':<22} {'Reqs':>6} {'Pass':>6} {'Fail':>6} {'Min(ms)':>9} {'Median(ms)':>11} {'Mean(ms)':>10} {'P95(ms)':>9} {'Max(ms)':>9}")
    print("-" * 93)
    for ep, stats in api_latencies.items():
        p95_str = f"{stats['p95_ms']:.4f}" if isinstance(stats['p95_ms'], (int, float)) else str(stats['p95_ms'])
        print(f"{ep:<22} {stats['sample_count']:>6} {stats['successful_requests']:>6} {stats['failed_requests']:>6} {stats['min_ms']:>9.4f} {stats['median_ms']:>11.4f} {stats['mean_ms']:>10.4f} {p95_str:>9} {stats['max_ms']:>9.4f}")

    print("\n  Controlled Local Concurrency Results:")
    print(f"  {'Concurrency Level':<25} {'Requests':>8} {'Throughput(req/s)':>18} {'Avg Latency(ms)':>16} {'P95 Latency(ms)':>16}")
    print("  " + "-" * 88)
    for ckey, cstats in concurrency_results.items():
        print(f"  {ckey:<25} {cstats['total_requests']:>8} {cstats['throughput_rps']:>18.2f} {cstats['mean_ms']:>16.4f} {cstats['p95_ms']:>16.4f}")

    print("\n[6/8] Validating Idempotency & Duplicate Prevention...")
    idem = runner.validate_idempotency()
    print(f"  Tx Hash Tested       : {idem['test_tx_hash']}")
    print(f"  Decisions Before     : {idem['counts_before']['decisions']}")
    print(f"  Decisions Post-Req 1 : {idem['counts_after_request_1']['decisions']} (+{idem['decisions_added_req1']})")
    print(f"  Decisions Post-Req 2 : {idem['counts_after_request_2']['decisions']} (+{idem['decisions_added_req2']})")
    print(f"  Idempotency Result   : {'PASSED' if idem['idempotency_verified'] else 'FAILED'}")

    print("\n[7/8] Validating Failure Handling & Safe Fallback...")
    err = runner.validate_error_and_fallback_behavior()
    print(f"  Model Failure Fallback  : Decision={err['model_failure_fallback']['decision']} (Audit={err['model_failure_fallback']['audit_event_type']}) -> Passed: {err['model_failure_fallback']['is_fallback']}")
    print(f"  HTTP 422 Bad Hash Check : Status={err['http_422_validation']['status_code']} -> Passed: {err['http_422_validation']['passed']}")
    print(f"  HTTP Unknown Model Check: Status={err['http_unknown_model']['status_code']} -> Passed: {err['http_unknown_model']['passed']}")
    print(f"  HTTP 404 Audit Lookup   : Status={err['http_404_audit_lookup']['status_code']} -> Passed: {err['http_404_audit_lookup']['passed']}")

    print("\n[8/8] Verifying Data & Model Artifact Integrity...")
    integrity = runner.verify_data_integrity()
    print(f"  M5 Dataset Intact       : {integrity['m5_dataset_intact']} ({integrity['m5_dataset_size_bytes']} bytes)")
    print(f"  M6 Production Models    : {integrity['m6_production_models_intact']}")
    print(f"  M10 Reports Preserved   : {integrity['m10_reports_preserved']}")

    # Assemble and save report
    total_runtime_s = round(time.time() - t_start, 2)
    full_report = {
        "milestone": "M11",
        "objective": "Latency and End-to-End Validation",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_benchmark_runtime_seconds": total_runtime_s,
        "environment": env,
        "end_to_end_trace": trace,
        "component_latencies": comp_latencies,
        "model_latencies": model_latencies,
        "api_latencies": api_latencies,
        "end_to_end_api_latency": e2e_api_summary,
        "concurrency_benchmarks": concurrency_results,
        "idempotency_validation": idem,
        "error_and_fallback_validation": err,
        "data_integrity": integrity,
        "limitations_and_disclaimer": {
            "context": "Local proof-of-concept benchmark executed on Windows 11 host with local Anvil node and SQLite WAL.",
            "disclaimer": "Simulated pre-confirmation risk scoring. Measurements do not represent public distributed mainnet consensus or production scale.",
            "dataset_note": "Synthetic traffic scenarios (M2) and pre-confirmation feature representations (M4).",
        },
    }
    save_validation_reports(full_report)

    print("\n" + "=" * 80)
    print("M11 VALIDATION REPORTS SAVED SUCCESSFULLY:")
    print("  - reports/m11_validation_report.json")
    print("  - reports/m11_validation_report.csv")
    print("  - reports/m11_validation_summary.md")
    print(f"Total Benchmark Wall Time: {total_runtime_s} seconds")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
