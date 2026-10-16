# MempoolGuard — Milestone M11 Latency & End-to-End Validation Report

> **Generated**: 2026-10-01T06:16:21Z  
> **Total Execution Time**: 7.59 seconds  
> **Notice**: Local proof-of-concept benchmark. Not evidence of public production mainnet performance.

---

## 1. Environment Specifications
- **OS**: Windows-11-10.0.26200-SP0
- **CPU**: 12th Gen Intel(R) Core(TM) i5-12450H (12 logical processors)
- **Memory**: 2.85 GB Free / 15.73 GB Total
- **Python**: 3.14.6
- **FastAPI**: 0.141.1 | **Uvicorn**: 0.53.0 | **Streamlit**: 1.64.0
- **Scikit-Learn**: 1.9.1 | **XGBoost**: 3.4.1 | **Pandas**: 3.0.6 | **Web3**: 8.0.0
- **SQLite**: 3.50.4 (WAL mode)
- **Anvil Node**: anvil Version: 1.8.3
Commit SHA: cae51ad458f6abb64852b7709eb784352429825d
Build Timestamp: 2026-09-15T10:30:41.249604900Z (1789468241)
Build Profile: dist (Connected: False)

---

## 2. Live End-to-End Pipeline Trace
- **Transaction Hash**: `0x751b47db283d32a8ba24470ba0036c24aa68479e46cf00f552a25c00a35396a7`
- **Sender**: `0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc`
- **Receiver**: `0x90f79bf6eb2c4f870365e785982e1f101e93b906`
- **Model**: `xgboost` v1.0.0
- **Risk Score Inferred**: **88.9532**
- **Decision Issued**: **`HOLD`** (Simulated: True)
- **Reason Codes**: `['HIGH_RISK_SCORE', 'REPEATED_TRANSFER_DETECTED', 'CONTRACT_CALL_INVOCATION']`
- **Audit ID**: `ae428eb5-0d47-479f-a956-2d2c4a8a1f48`
- **Terminal Confirmed On-Chain**: `True`

### Stage Timestamps & Continuity
| Pipeline Stage | UTC ISO-8601 Timestamp |
|:---|:---|
| `transaction_received` | `2026-10-01T06:16:19.636860+00:00` |
| `mempool_detected` (M3) | `2026-10-01T06:16:19.676539+00:00` |
| `transaction_decoded` | `2026-10-01T06:16:19.685576+00:00` |
| `features_created` (M4) | `2026-10-01T06:16:19.692039+00:00` |
| `prediction_created` (M6) | `2026-10-01T06:16:19.699402+00:00` |
| `decision_created` (M7) | `2026-10-01T06:16:19.707372+00:00` |
| `audit_persisted` (DB) | `2026-10-01T06:16:19.704528+00:00` |
| `api_retrieved` (M8) | `2026-10-01T06:16:19.713749+00:00` |

### Stage Durations
| Stage | Latency |
|:---|:---:|
| M3 Detection | 39.6675 ms |
| Transaction Decoding | 6.6486 ms |
| M4 Feature Extraction (30 features) | 6.4571 ms |
| M6 XGBoost Inference | 7.3487 ms |
| M7 Decision Policy & Audit Write | 7.9587 ms |
| API Record Lookup | 0.64 ms |
| **Total End-to-End Pipeline** | **70.5043 ms** |

---

## 3. Component Latency Benchmarks (N >= 20 Samples)
| Component / Operation | Samples | Min (ms) | Median (ms) | Mean (ms) | P95 (ms) | Max (ms) |
|:---|---:|---:|---:|---:|---:|---:|
| `M4_feature_extraction` | 25 | 2.1591 | 2.7835 | 2.7888 | 3.5093 | 3.6093 |
| `M6_rule_baseline` | 25 | 0.0851 | 0.0908 | 0.1042 | 0.1453 | 0.3471 |
| `M6_logistic_regression` | 25 | 0.6187 | 0.6851 | 0.9959 | 0.9262 | 8.1309 |
| `M6_xgboost` | 25 | 0.8345 | 0.9583 | 1.3519 | 1.2292 | 10.3155 |
| `M7_decision_policy` | 25 | 0.0022 | 0.0022 | 0.0032 | 0.0051 | 0.0222 |
| `database_audit_persistence` | 25 | 3.0987 | 3.8823 | 4.7859 | 9.3779 | 11.9536 |
| `M9_dataloader_retrieval` | 25 | 1.6406 | 1.8995 | 2.032 | 2.6948 | 3.0267 |

---

## 4. Model Inference Latency Benchmarks (N = 50 Samples)
| Model | Samples | Min (ms) | Median (ms) | Mean (ms) | P95 (ms) | Max (ms) |
|:---|---:|---:|---:|---:|---:|---:|
| **rule_baseline** | 50 | 0.0558 | 0.0616 | 0.0707 | 0.1479 | 0.166 |
| **logistic_regression** | 50 | 0.6097 | 0.6674 | 0.7164 | 0.93 | 1.1263 |
| **xgboost** | 50 | 0.8064 | 0.9417 | 1.2307 | 1.2206 | 14.3536 |

---

## 5. Live FastAPI Service Latency Benchmarks (N >= 20 HTTP Requests)
| Endpoint | Requests | Success | Failures | Min (ms) | Median (ms) | Mean (ms) | P95 (ms) | Max (ms) |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| `/health` | 25 | 25 | 0 | 5.8605 | 15.2974 | 15.5346 | 32.0913 | 32.4636 |
| `/predict` | 25 | 25 | 0 | 8.9569 | 9.9358 | 10.7398 | 12.0744 | 30.2413 |
| `/decision` | 25 | 25 | 0 | 7.49 | 8.3612 | 8.4822 | 9.2686 | 12.9283 |
| `/audit/{tx_hash}` | 25 | 25 | 0 | 2.8836 | 5.7611 | 5.1584 | 7.188 | 10.4644 |

---

## 6. Controlled Local Concurrency Benchmarks
| Concurrency Level | Requests | Success | Failures | Total Wall Time (s) | Throughput (req/s) | Avg Latency (ms) | P95 Latency (ms) |
|:---|---:|---:|---:|---:|---:|---:|---:|
| **5 Concurrent Workers** | 20 | 20 | 0 | 0.1688 | **118.47** | 28.9023 | 36.2252 |
| **10 Concurrent Workers** | 20 | 20 | 0 | 0.1957 | **102.2** | 71.5176 | 119.6174 |

---

## 7. Reliability, Idempotency & Error Handling
### Idempotency Verification
- **Test Transaction**: `0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeef61b56f3`
- **Initial Decisions Count**: `1038`
- **After Request 1**: `1039` (+1)
- **After Request 2 (Duplicate)**: `1039` (+0)
- **Duplicate Decision Prevented**: **`True`** (Idempotency Passed)

### Error & Safe Fallback Verification
- **Hardware/Model Failure Injection**: Fallback engaged -> `FLAG` (Reason: `['MODEL_FAILURE', 'FALLBACK_POLICY', 'MEDIUM_RISK_SCORE']`)
- **Pipeline Error Audited**: `PIPELINE_ERROR`
- **HTTP 422 Bad Hash Handled**: Status `422` (Sanitized: `True`)
- **HTTP Unknown Model Handled**: Status `404` (Sanitized: `True`)
- **HTTP 404 Missing Audit Handled**: Status `404` (Sanitized: `True`)
