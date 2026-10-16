"""Dedicated unit and integration tests for Milestone M11: Latency and End-to-End Validation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from benchmark.runner import M11ValidationRunner, save_validation_reports
from benchmark.timer import HighResolutionTimer


def test_high_resolution_timer_statistics_and_percentiles():
    """Verify statistical summary calculations with >= 20 samples."""
    timer = HighResolutionTimer("test_timer")
    for i in range(1, 26):  # 25 samples: 1.0 to 25.0
        timer.record(float(i))

    summary = timer.get_summary()
    assert summary["sample_count"] == 25
    assert summary["min_ms"] == 1.0
    assert summary["max_ms"] == 25.0
    assert summary["mean_ms"] == 13.0
    assert summary["median_ms"] == 13.0
    assert isinstance(summary["p95_ms"], float)
    assert summary["p95_note"] is None


def test_timing_sample_size_edge_cases():
    """Verify that sample sizes < 20 return 'N/A' for p95 without crashing."""
    # 0 samples
    timer_empty = HighResolutionTimer("empty")
    summary_empty = timer_empty.get_summary()
    assert summary_empty["sample_count"] == 0
    assert summary_empty["p95_ms"] == "N/A"

    # 10 samples (< 20)
    timer_small = HighResolutionTimer("small")
    for i in range(10):
        timer_small.record(float(i))
    summary_small = timer_small.get_summary()
    assert summary_small["sample_count"] == 10
    assert summary_small["p95_ms"] == "N/A"
    assert "Sample size 10 < 20" in summary_small["p95_note"]


def test_end_to_end_trace_structure_and_continuity():
    """Verify end-to-end trace structure, keys, and duration positivity."""
    runner = M11ValidationRunner()
    trace = runner.validate_end_to_end_trace()

    required_keys = [
        "tx_hash",
        "sender",
        "receiver",
        "timestamps",
        "stage_durations_ms",
        "model_name",
        "model_version",
        "feature_schema_version",
        "feature_count",
        "risk_score",
        "decision",
        "reason_codes",
        "audit_id",
        "is_simulated",
        "terminal_confirmed",
    ]
    for key in required_keys:
        assert key in trace, f"Missing key in trace: {key}"

    assert trace["feature_count"] == 30
    assert trace["is_simulated"] is True
    assert trace["decision"] in ["ALLOW", "FLAG", "HOLD"]

    # Durations must be non-negative
    for stage, dur in trace["stage_durations_ms"].items():
        assert dur >= 0.0, f"Duration for {stage} is negative: {dur}"


def test_component_latency_measurement_integrity():
    """Verify component latencies can be measured across all stages."""
    runner = M11ValidationRunner()
    results = runner.benchmark_component_latencies(sample_count=5)

    expected_components = [
        "M4_feature_extraction",
        "M6_rule_baseline",
        "M6_logistic_regression",
        "M6_xgboost",
        "M7_decision_policy",
        "database_audit_persistence",
        "M9_dataloader_retrieval",
    ]
    for comp in expected_components:
        assert comp in results
        stats = results[comp]
        assert stats["sample_count"] == 5
        assert stats["mean_ms"] > 0.0


def test_idempotency_verification_logic():
    """Verify that repeated transactions do not produce duplicate decisions."""
    runner = M11ValidationRunner()
    idem = runner.validate_idempotency()

    assert idem["duplicate_prevented"] is True
    assert idem["decisions_added_req1"] == 1
    assert idem["decisions_added_req2"] == 0


def test_error_and_fallback_resilience():
    """Verify safe fallback to FLAG upon model exception and 422/404 handling."""
    runner = M11ValidationRunner(port=8089)
    err = runner.validate_error_and_fallback_behavior()

    # Hardware/Model exception fallback
    fb = err["model_failure_fallback"]
    assert fb["decision"] == "FLAG"
    assert fb["is_fallback"] is True
    assert "MODEL_FAILURE" in fb["reason_codes"] or "FALLBACK_POLICY" in fb["reason_codes"]
    assert fb["audit_event_type"] == "PIPELINE_ERROR"

    # HTTP validations
    assert err["http_422_validation"]["passed"] is True
    assert err["http_422_validation"]["sanitized"] is True
    assert err["http_404_audit_lookup"]["passed"] is True
    assert err["http_404_audit_lookup"]["sanitized"] is True


def test_report_generation_and_schema():
    """Verify report generation produces valid JSON, CSV, and Markdown files."""
    runner = M11ValidationRunner()
    report = {
        "milestone": "M11",
        "timestamp": "2026-10-01T12:00:00Z",
        "total_benchmark_runtime_seconds": 1.23,
        "environment": runner.inspect_environment(),
        "end_to_end_trace": {
            "tx_hash": "0x" + "11" * 32,
            "sender": "0x" + "22" * 20,
            "receiver": "0x" + "33" * 20,
            "model_name": "xgboost",
            "model_version": "1.0.0",
            "risk_score": 10.5,
            "decision": "ALLOW",
            "reason_codes": ["LOW_RISK_SCORE"],
            "audit_id": "aud-123",
            "is_simulated": True,
            "terminal_confirmed": True,
            "timestamps": {
                "transaction_received": "2026-10-01T12:00:00Z",
                "mempool_detected": "2026-10-01T12:00:00.01Z",
                "transaction_decoded": "2026-10-01T12:00:00.02Z",
                "features_created": "2026-10-01T12:00:00.03Z",
                "prediction_created": "2026-10-01T12:00:00.04Z",
                "decision_created": "2026-10-01T12:00:00.05Z",
                "audit_persisted": "2026-10-01T12:00:00.06Z",
                "api_retrieved": "2026-10-01T12:00:00.07Z",
            },
            "stage_durations_ms": {
                "m3_detection_ms": 1.0,
                "transaction_decoding_ms": 1.0,
                "m4_feature_extraction_ms": 1.0,
                "m6_inference_ms": 1.0,
                "m7_decision_and_audit_ms": 1.0,
                "api_lookup_ms": 1.0,
                "total_end_to_end_ms": 6.0,
            },
        },
        "component_latencies": {},
        "model_latencies": {},
        "api_latencies": {},
        "concurrency_benchmarks": {},
        "idempotency_validation": {
            "test_tx_hash": "0x" + "11" * 32,
            "counts_before": {"decisions": 0},
            "counts_after_request_1": {"decisions": 1},
            "counts_after_request_2": {"decisions": 1},
            "decisions_added_req1": 1,
            "decisions_added_req2": 0,
            "duplicate_prevented": True,
        },
        "error_and_fallback_validation": {
            "model_failure_fallback": {
                "decision": "FLAG",
                "reason_codes": ["MODEL_FAILURE_FALLBACK"],
                "audit_event_type": "PIPELINE_ERROR",
            },
            "http_422_validation": {"status_code": 422, "sanitized": True},
            "http_unknown_model": {"status_code": 400, "sanitized": True},
            "http_404_audit_lookup": {"status_code": 404, "sanitized": True},
        },
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        j_path = str(Path(tmpdir) / "report.json")
        c_path = str(Path(tmpdir) / "report.csv")
        m_path = str(Path(tmpdir) / "report.md")

        save_validation_reports(report, json_path=j_path, csv_path=c_path, md_path=m_path)

        assert Path(j_path).exists() and Path(j_path).stat().st_size > 0
        assert Path(c_path).exists()
        assert Path(m_path).exists() and Path(m_path).stat().st_size > 0

        with open(j_path, encoding="utf-8") as f:
            loaded_json = json.load(f)
            assert loaded_json["milestone"] == "M11"
