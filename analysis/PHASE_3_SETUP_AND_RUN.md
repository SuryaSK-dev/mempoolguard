# MempoolGuard — Phase 3: Setup & Run Report

> **DISCLAIMER**: Synthetic proof-of-concept labels; not evidence of real-world detection performance.
> ALLOW, FLAG, and HOLD are application-level simulated decisions. They do not modify blockchain consensus or guarantee transaction interception.

---

## 1. Environment Provisioning & Tooling

### Python Environment
- **Host Runtime**: Python 3.11.9 (64-bit on Windows `win32`)
- **Virtual Environment**: Isolated environment created at `c:\Users\USER\Downloads\PD-PROJECT\.venv` via `python -m venv c:\Users\USER\Downloads\PD-PROJECT\.venv`.
- **Primary Dependencies Installed** from [`miniproject/requirements.txt`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/requirements.txt):
  - `pydantic 2.13.5`, `pyyaml 6.0.3`, `python-dotenv 1.2.4`, `web3 8.0.0`, `pandas 3.0.6`, `numpy 2.4.6`, `networkx 3.6.1`, `pyarrow 25.0.1`, `scikit-learn 1.9.1`, `xgboost 3.2.0`, `fastapi 0.142.2`, `uvicorn 0.54.0`, `streamlit 1.64.0`, `plotly 7.1.0`, `pytest 9.1.1`, `pytest-asyncio 1.4.0`, `ruff 0.16.10`.

### Local Blockchain Node (Foundry Anvil)
- **Installation Method**: Official Foundryup script executed via Git Bash:
  ```bash
  curl -L https://foundry.paradigm.xyz | bash
  ~/.foundry/bin/foundryup
  ```
- **Binary Location**: `C:\Users\USER\.foundry\bin\anvil.exe` (automatically detected by [`miniproject/core/anvil.py#L47`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/core/anvil.py#L47)).
- **Verified Version**:
  ```
  anvil Version: 1.8.4
  Commit SHA: 50af4efe189dc64bad2b75ed6990b835de66c4ae
  Build Timestamp: 2026-10-01T13:50:42.600348400Z (1790862642)
  Build Profile: dist
  ```
- **Node Status**: Active and listening on `127.0.0.1:8545` (Chain ID `31337`).

---

## 2. Issues Encountered & Approved Remediation

### Missing Dependency: `httpx`
- **Initial Symptom**: First `pytest tests/ -v` run halted during collection with 4 errors across:
  - [`tests/test_dashboard.py#L24`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_dashboard.py#L24) $\to$ [`dashboard/api_client.py#L8`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/dashboard/api_client.py#L8) (`ModuleNotFoundError: No module named 'httpx'`)
  - [`tests/test_m11_validation.py#L9`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_m11_validation.py#L9) $\to$ [`benchmark/runner.py#L31`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/benchmark/runner.py#L31) (`ModuleNotFoundError: No module named 'httpx'`)
  - [`tests/test_api.py#L22`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_api.py#L22) (`RuntimeError: The starlette.testclient module requires the httpx2 package to be installed.`)
  - [`tests/test_m12_integration.py#L10`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_m12_integration.py#L10) (`RuntimeError: The starlette.testclient module requires the httpx2 package to be installed.`)
- **Root Cause**: `httpx` is directly imported in `dashboard/api_client.py`, `benchmark/runner.py`, and `scripts/run_final_demo.py`, and required by Starlette's `TestClient`. It was omitted from `miniproject/requirements.txt`.
- **Approved Fix**: Installed `httpx` (`v0.28.1`) in `.venv` without modifying `miniproject/requirements.txt`.
- **Import Verification**:
  ```powershell
  & "c:\Users\USER\Downloads\PD-PROJECT\.venv\Scripts\python.exe" -c "from starlette.testclient import TestClient; import httpx; print(httpx.__version__)"
  ```
  Output: `0.28.1` (Clean import with non-blocking deprecation notice).

---

## 3. System Diagnostics

Diagnostic check executed via:
```powershell
& "c:\Users\USER\Downloads\PD-PROJECT\.venv\Scripts\python.exe" -m core.health
```

**Diagnostic Results**:
```json
{
  "overall_status": "HEALTHY",
  "python": {
    "status": "HEALTHY",
    "python_version": "3.11.9",
    "platform": "win32",
    "is_supported": true
  },
  "database": {
    "status": "HEALTHY",
    "database_path": "data/mempoolguard.db",
    "table_counts": {
      "raw_transactions": 140,
      "decoded_transactions": 54,
      "feature_vectors": 58,
      "model_predictions": 79,
      "decisions": 1077,
      "audit_records": 1077
    }
  },
  "blockchain_rpc": {
    "status": "HEALTHY",
    "rpc_url": "http://127.0.0.1:8545",
    "connected": true,
    "client_version": "anvil/v1.8.4"
  },
  "storage": {
    "status": "HEALTHY",
    "directories": {
      "data\\raw": "writable",
      "data\\processed": "writable",
      "artifacts\\models": "writable"
    }
  }
}
```

---

## 4. Test Suite Execution Results

Executed command:
```powershell
& "c:\Users\USER\Downloads\PD-PROJECT\.venv\Scripts\python.exe" -m pytest tests/ -v
```

### Aggregate Metrics
- **Total Tests Collected**: 110
- **Passed**: 110 (100.0%)
- **Failed**: 0
- **Skipped**: 0
- **Warnings**: 4 (Deprecation warnings in third-party libraries: `starlette.testclient`, `pickle.py` loading XGBoost, and HTTP 422 status code constant)
- **Total Duration**: 97.40 seconds

### Module-by-Module Breakdown

| Test Module | Tests Passed | Live Node Used? | Coverage Focus |
|:---|:---:|:---:|:---|
| [`tests/test_api.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_api.py) | 12 / 12 | Simulated & DB | OpenAPI specs, `/health`, `/predict`, `/decision`, `/audit`, validation, latency (<50ms). |
| [`tests/test_config.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_config.py) | 5 / 5 | Unit | Default YAML loading, environment overrides, threshold invariants, URI validation. |
| [`tests/test_contracts.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_contracts.py) | 7 / 7 | Unit | Pydantic validation on `Transaction`, `FeatureVector`, `RiskResult`, `Decision`, `AuditRecord`. |
| [`tests/test_dashboard.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_dashboard.py) | 6 / 6 | Unit & Mock | `DataLoader` querying SQLite, KPI distributions, `ApiClient` HTTP communications. |
| [`tests/test_database.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_database.py) | 8 / 8 | Unit (SQLite WAL) | Table initialization, deduplication, atomic rollback, pagination, foreign key cascades. |
| [`tests/test_dataset.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_dataset.py) | 10 / 10 | Unit | Parquet/CSV dataset builder, quality checks, chronological zero-leakage splits. |
| [`tests/test_decision.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_decision.py) | 8 / 8 | Unit & Integration | Boundary semantics, reason codes, idempotency, safe fallbacks on invalid/NaN scores. |
| [`tests/test_features.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_features.py) | 10 / 10 | Unit & Integration | 30 canonical features (Tx, Gas, History, Graph), missing values, anti-leakage invariants. |
| [`tests/test_generator.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_generator.py) | 10 / 10 | **YES (Live Anvil Node)** | Anvil test accounts, lifecycle, gas spikes, burst, fan-out, contract calls, manifest generation. |
| [`tests/test_health.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_health.py) | 5 / 5 | Unit & RPC | Python version, SQLite health, offline/online RPC diagnostics, storage directory write check. |
| [`tests/test_listener.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_listener.py) | 6 / 6 | **YES (Live Anvil Node)** | Poller lifecycle, JSON serialization, hash deduplication, live pending mempool capture. |
| [`tests/test_m10_ablation.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_m10_ablation.py) | 6 / 6 | Unit & ML | Leave-one-group-out feature ablation across 6 groups, report persistence. |
| [`tests/test_m11_validation.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_m11_validation.py) | 7 / 7 | Unit & Benchmark | Nanosecond timer, percentiles, end-to-end trace structure, idempotency benchmarks. |
| [`tests/test_m12_integration.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_m12_integration.py) | 5 / 5 | **YES (Live Anvil Node)** | Full stack prerequisites, trace schema completeness, report generation. |
| [`tests/test_models.py`](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/test_models.py) | 7 / 7 | Unit & ML | RuleBaseline, LogisticRegression, XGBoost fit/predict, artifact reload, label-leakage audit. |

---

## 5. Artifact & Run Log References
- All executed commands, outputs, and stack traces are recorded chronologically in [`analysis/run_log.txt`](file:///c:/Users/USER/Downloads/PD-PROJECT/analysis/run_log.txt).
- Background Anvil process is active and running on `127.0.0.1:8545` for Phase 4 live execution.
