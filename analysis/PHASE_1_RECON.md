# MempoolGuard — Phase 1: Project Reconnaissance Report

> **DISCLAIMER**: Synthetic proof-of-concept labels; not evidence of real-world detection performance.
> ALLOW, FLAG, and HOLD are application-level simulated decisions. They do not modify blockchain consensus or guarantee transaction interception.

---

## 1. Executive Summary & Purpose

- **Project Name**: MempoolGuard: Machine Learning-Based Pre-Confirmation Cryptocurrency Transaction Risk Detection
- **Purpose**: MempoolGuard is an experimental research prototype designed to inspect pending Ethereum-compatible transactions in the mempool before block confirmation. It deterministically computes 30 canonical pre-confirmation features (transaction-level, gas metrics, address history, burst/window statistics, and lightweight graph metrics), evaluates them with machine learning classifiers (XGBoost, Logistic Regression, Rule Baseline), applies an application-level simulated risk policy (`ALLOW` < 30.0 <= `FLAG` <= 70.0 < `HOLD`), and logs immutable relational audit records to an ACID SQLite database. It serves these predictions via a FastAPI REST API and provides observability through an interactive Streamlit security operations dashboard.
- **Reference**: [miniproject/README.md](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/README.md#L1-L24), [miniproject/AGENT_RULES.md](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/AGENT_RULES.md#L1-L25).

---

## 2. Repository Layout & File Tree

The codebase is organized under `miniproject/` within the root workspace `c:\Users\USER\Downloads\PD-PROJECT`:

```
c:\Users\USER\Downloads\PD-PROJECT
├── analysis/                               # Analysis reports, diagrams, logs
├── miniproject/
│   ├── .env.example                        # Safe default environment overrides
│   ├── .gitignore                          # Standard git ignore definitions
│   ├── AGENT_RULES.md                      # Safety & experimental integrity rules
│   ├── PROJECT_WALKTHROUGH.md              # Milestone M1-M12 implementation audit
│   ├── README.md                           # System overview & CLI quickstart
│   ├── requirements.txt                    # Python runtime dependencies
│   ├── api/                                # FastAPI application & REST routes
│   │   ├── app.py                          # App factory, CORS, exception handlers
│   │   ├── dependencies.py                 # Dependency injection (DB, config, models)
│   │   ├── schemas.py                      # Pydantic request & response contracts
│   │   └── routes/                         # Route handlers
│   │       ├── health.py                   # GET /health diagnostics
│   │       ├── predict.py                  # POST /predict risk scoring
│   │       ├── decision.py                 # POST /decision, POST /decision/simulate
│   │       └── audit.py                    # GET /audit/{tx_hash} query
│   ├── artifacts/                          # Model & evaluation persistent artifacts
│   ├── benchmark/                          # Latency & throughput timing harnesses
│   │   ├── runner.py                       # Benchmark runner
│   │   └── timer.py                        # High-resolution nanosecond timer
│   ├── collector/                          # Mempool observation & traffic generation
│   │   ├── mempool_listener.py             # Pending tx poller & raw payload capture
│   │   └── transaction_generator.py        # 8 synthetic attack & benign scenarios
│   ├── configs/
│   │   └── config.yaml                     # Primary YAML system configuration
│   ├── core/                               # Foundational contracts, DB, logging
│   │   ├── anvil.py                        # Local Anvil EVM node process controller
│   │   ├── config.py                       # Pydantic schema validation & env loader
│   │   ├── contracts.py                    # Validated data models (Tx, Features, Risk, Decision, Audit)
│   │   ├── database.py                     # SQLite transactional manager (WAL mode)
│   │   ├── health.py                       # Composite system diagnostics
│   │   └── logging_config.py               # Structured logging configuration
│   ├── dashboard/                          # Streamlit UI
│   │   ├── app.py                          # Main dashboard entry point & navigation
│   │   ├── api_client.py                   # HTTP client querying FastAPI service
│   │   ├── data_loader.py                  # Direct read-only SQLite connector
│   │   └── components/                     # 8 modular UI views
│   │       ├── overview.py                 # System KPI overview
│   │       ├── transactions.py             # Transaction table & filters
│   │       ├── risk.py                     # Risk distribution & score breakdown
│   │       ├── decisions.py                # Intervention actions (ALLOW/FLAG/HOLD)
│   │       ├── audit.py                    # Relational audit trail explorer
│   │       ├── models.py                   # Model registry & metadata inspector
│   │       ├── analytics.py                # Policy threshold & latency analytics
│   │       └── tx_detail.py                # Single transaction deep inspector
│   ├── data/                               # Local persistent storage
│   │   ├── mempoolguard.db                 # Primary production SQLite database
│   │   ├── mempoolguard_expanded.db        # Benchmark SQLite database
│   │   ├── raw/                            # Synthetic scenario manifests
│   │   │   ├── synthetic_scenarios.json
│   │   │   └── synthetic_scenarios_expanded.json
│   │   └── processed/                      # Parquet / CSV datasets & splits
│   ├── dataset/                            # Dataset construction & splitting
│   │   ├── builder.py                      # Joins features with scenario labels
│   │   ├── quality.py                      # Data validation checks
│   │   ├── schema.py                       # 30-feature canonical schema constants
│   │   └── splitter.py                     # Chronological & stratified splitters
│   ├── decision/                           # Policy & intervention engine
│   │   ├── engine.py                       # Decision orchestrator
│   │   ├── policy.py                       # Threshold logic & fallback handling
│   │   └── audit.py                        # Audit trail persistence
│   ├── docs/                               # Architectural documentation
│   │   ├── architecture.md
│   │   ├── limitations.md
│   │   └── patent/lab_notebook.md
│   ├── features/                           # Deterministic feature engineering
│   │   ├── decoder.py                      # Raw hex payload decoder
│   │   └── engine.py                       # 30 canonical pre-confirmation features
│   ├── models/                             # Machine learning models & evaluation
│   │   ├── baseline.py                     # Rule-based heuristic baseline
│   │   ├── logistic.py                     # Scaled Logistic Regression
│   │   ├── xgboost_model.py                # Production XGBoost classifier
│   │   ├── evaluator.py                    # Classification metrics & reports
│   │   ├── persistence.py                  # Joblib serializer & schema validator
│   │   ├── audit.py                        # Anti-leakage feature audit
│   │   ├── ablation.py                     # Feature group ablation experiment
│   │   └── saved/                          # Trained models (.joblib) & metadata
│   ├── reports/                            # Milestone verification reports (M10-M12)
│   ├── scripts/                            # Verification runners & final demo
│   │   ├── run_final_demo.py               # Complete M1-M12 integration demo
│   │   ├── run_m10_ablation.py             # Feature ablation runner
│   │   ├── run_m11_validation.py           # End-to-end latency validation
│   │   ├── verify_m3_experiment.py         # M3 mempool listener verification
│   │   ├── verify_m4_experiment.py         # M4 feature engine verification
│   │   ├── verify_m5_experiment.py         # M5 dataset builder verification
│   │   ├── verify_m6_experiment.py         # M6 ML models & audit verification
│   │   ├── verify_m7_experiment.py         # M7 simulated decision verification
│   │   ├── verify_m7_genuine_m6.py         # M7 genuine M6 -> M7 verification
│   │   ├── verify_m8_server.py             # M8 FastAPI HTTP verification
│   │   └── verify_m9_dashboard.py          # M9 Streamlit HTTP verification
│   └── tests/                              # Pytest test suite (16 test files)
```

---

## 3. Technology Stack & Dependencies

### Runtime & Language
- **Language**: Python 3.11+ ([miniproject/README.md#L16](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/README.md#L16))
- **Host Runtime Verified**: Python 3.11.9 64-bit on Windows (`python --version` confirmed: `Python 3.11.9`)

### Core Libraries ([miniproject/requirements.txt#L1-L29](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/requirements.txt#L1-L29))
- **Contracts & Validation**: `pydantic>=2.4.0`, `pyyaml>=6.0.0`, `python-dotenv>=1.0.0`
- **Blockchain Connectivity**: `web3>=6.15.0`
- **Data Engineering**: `pandas>=2.0.0`, `numpy>=1.24.0`, `networkx>=3.0.0`, `pyarrow>=14.0.0`
- **Machine Learning**: `scikit-learn>=1.3.0`, `xgboost>=2.0.0`
- **API & UI**: `fastapi>=0.104.0`, `uvicorn>=0.24.0`, `streamlit>=1.28.0`, `plotly>=5.15.0`
- **Testing & Quality**: `pytest>=7.4.0`, `pytest-asyncio>=0.21.0`, `ruff>=0.1.0`
- **Embedded Database**: SQLite 3 with Write-Ahead Logging (`WAL`) mode enabled ([miniproject/core/database.py#L82](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/core/database.py#L82))

### External Infrastructure & Tools
- **EVM Blockchain Node**: Foundry Anvil (`anvil --port 8545`, Chain ID: 31337).
  - *Host Verification*: `anvil : The term 'anvil' is not recognized`. Anvil binary is currently not detected on system PATH or default `~/.foundry/bin` directory. The codebase implements graceful fallbacks (`status: DEGRADED`) in health checks ([miniproject/core/health.py#L148](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/core/health.py#L148)) and unit tests.
- **Container / Orchestration Files**:
  - `Dockerfile`: **NONE FOUND** (Verified by directory recursion)
  - `docker-compose.yml`: **NONE FOUND** (Verified by directory recursion)
  - `pyproject.toml` / `setup.py`: **NONE FOUND** (Standard pip / virtualenv model)

---

## 4. Configuration & Environment Variables

System configuration is declared hierarchically in [miniproject/configs/config.yaml](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/configs/config.yaml#L1-L50) and validated using Pydantic in [miniproject/core/config.py](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/core/config.py#L174-L255).

### Environment Variable Overrides ([miniproject/.env.example#L1-L29](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/.env.example#L1-L29))

| Environment Variable | Config Path | Default Value | Description |
|:---|:---|:---|:---|
| `MEMPOOLGUARD_RPC_HTTP_URL` | `blockchain.rpc_http_url` | `http://127.0.0.1:8545` | EVM JSON-RPC HTTP endpoint |
| `MEMPOOLGUARD_RPC_WS_URL` | `blockchain.rpc_ws_url` | `ws://127.0.0.1:8545` | EVM WebSocket endpoint |
| `MEMPOOLGUARD_CHAIN_ID` | `blockchain.chain_id` | `31337` | EVM Chain ID (Anvil default) |
| `MEMPOOLGUARD_SQLITE_PATH` | `database.sqlite_path` | `data/mempoolguard.db` | Relative path to SQLite DB |
| `MEMPOOLGUARD_DB_TIMEOUT_SECONDS` | `database.timeout_seconds` | `10.0` | SQLite lock timeout (seconds) |
| `MEMPOOLGUARD_FEATURE_WINDOW_SECONDS` | `features.window_seconds` | `60.0` | Temporal feature aggregation window |
| `MEMPOOLGUARD_FEATURE_VERSION` | `features.feature_version` | `1.0.0` | Semantic version of feature vector |
| `MEMPOOLGUARD_THRESHOLD_ALLOW` | `risk.threshold_allow` | `30.0` | Risk score ceiling for `ALLOW` |
| `MEMPOOLGUARD_THRESHOLD_FLAG` | `risk.threshold_flag` | `70.0` | Risk score ceiling for `FLAG` (`>70` = `HOLD`) |
| `MEMPOOLGUARD_DEFAULT_FALLBACK_DECISION` | `risk.default_fallback_decision` | `FLAG` | Safe fallback if inference fails |
| `MEMPOOLGUARD_POLICY_VERSION` | `risk.policy_version` | `1.0.0` | Policy ruleset semantic version |
| `MEMPOOLGUARD_API_HOST` | `api.host` | `127.0.0.1` | FastAPI listening IP |
| `MEMPOOLGUARD_API_PORT` | `api.port` | `8000` | FastAPI listening port |
| `MEMPOOLGUARD_LOG_LEVEL` | `logging.level` | `INFO` | Logging verbosity |

*Note on Credentials*: No cloud API keys, private wallet keys, or external secrets are required. All blockchain tests use standard local deterministic Anvil test accounts ([miniproject/AGENT_RULES.md#L6-L10](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/AGENT_RULES.md#L6-L10)).

---

## 5. Entry Points Inventory

### A. CLI Commands & Module Runners

1. **System Health Check**:
   - Command: `python -m core.health`
   - Source: [miniproject/core/health.py#L161-L164](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/core/health.py#L161-L164)
   - Function: Runs composite diagnostic suite (Python version, SQLite connectivity, RPC node status, storage directories) and prints formatted JSON.

2. **Synthetic Transaction Traffic Generator**:
   - Command: `python -m collector.transaction_generator --rpc <url> --seed <int> --manifest <path>`
   - Source: [miniproject/collector/transaction_generator.py#L490-L530](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/collector/transaction_generator.py#L490-L530)
   - Function: Dispatches 8 reproducible synthetic and benign transaction scenarios (34 transactions) to the local Anvil node.

3. **Mempool Pending Transaction Listener**:
   - Command: `python -m collector.mempool_listener --rpc <url> --db <path> --interval <float> --duration <float>`
   - Source: [miniproject/collector/mempool_listener.py#L320-L367](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/collector/mempool_listener.py#L320-L367)
   - Function: Polls local EVM node for pending mempool transactions, deduplicates, stores raw JSON payloads, and timestamps observations.

4. **Deterministic Feature Extraction Engine**:
   - Command: `python -m features.engine --hash <0x...hash> --db <path>`
   - Source: [miniproject/features/engine.py#L374-L401](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/features/engine.py#L374-L401)
   - Function: Extracts the canonical 30 pre-confirmation features for an individual transaction from SQLite raw transactions.

5. **ML-Ready Dataset Builder**:
   - Command: `python -m dataset.builder --manifest <path> --output-parquet <path> --output-csv <path>`
   - Source: [miniproject/dataset/builder.py#L280-L323](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/dataset/builder.py#L280-L323)
   - Function: Combines extracted feature vectors with ground truth scenario labels into Parquet and CSV files with schema validation.

### B. Network Services & User Interfaces

6. **FastAPI REST Service (ASGI)**:
   - Command: `python -m uvicorn api.app:app --host 127.0.0.1 --port 8000`
   - Source: [miniproject/api/app.py#L34-L111](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/api/app.py#L34-L111)
   - Endpoints:
     - `GET /health`: Multi-subsystem health check ([miniproject/api/routes/health.py#L18-L68](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/api/routes/health.py#L18-L68))
     - `POST /predict`: Risk inference on transaction payload or raw feature dictionary ([miniproject/api/routes/predict.py#L59-L148](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/api/routes/predict.py#L59-L148))
     - `POST /decision`: Evaluates risk policy and writes immutable audit record ([miniproject/api/routes/decision.py#L61-L115](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/api/routes/decision.py#L61-L115))
     - `POST /decision/simulate`: Simulates policy against hypothetical scores ([miniproject/api/routes/decision.py#L117-L148](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/api/routes/decision.py#L117-L148))
     - `GET /audit/{tx_hash}`: Query decisions and audit trail for a hash ([miniproject/api/routes/audit.py#L16-L103](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/api/routes/audit.py#L16-L103))

7. **Streamlit Security Operations Dashboard**:
   - Command: `python -m streamlit run dashboard/app.py --server.port 8501`
   - Source: [miniproject/dashboard/app.py#L22-L122](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/dashboard/app.py#L22-L122)
   - Views: System Overview, Transaction Monitoring, Risk Monitoring, Decision Monitoring, Audit Explorer, Model Registry, Risk & Policy Analytics, Transaction Inspector.

### C. Orchestration & Verification Scripts

8. **End-to-End Milestone Demonstration**:
   - Command: `python scripts/run_final_demo.py`
   - Source: [miniproject/scripts/run_final_demo.py#L1-L850](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/scripts/run_final_demo.py#L1-L850)
   - Function: Orchestrates live Anvil, listener, feature extractor, XGBoost model, decision engine, SQLite audit, FastAPI, and Streamlit.

9. **Milestone Ablation Experiment (M10)**:
   - Command: `python scripts/run_m10_ablation.py`
   - Source: [miniproject/scripts/run_m10_ablation.py](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/scripts/run_m10_ablation.py)

10. **Milestone Latency & Validation Benchmark (M11)**:
    - Command: `python scripts/run_m11_validation.py`
    - Source: [miniproject/scripts/run_m11_validation.py](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/scripts/run_m11_validation.py)

11. **Milestone Verification Scripts (M3 through M9)**:
    - `scripts/verify_m3_experiment.py` (Mempool listener lifecycle)
    - `scripts/verify_m4_experiment.py` (Feature engine & anti-leakage)
    - `scripts/verify_m5_experiment.py` (Dataset builder & splitting)
    - `scripts/verify_m6_experiment.py` (Model training, evaluation, persistence)
    - `scripts/verify_m7_experiment.py` & `scripts/verify_m7_genuine_m6.py` (Decision engine & audit)
    - `scripts/verify_m8_server.py` (FastAPI HTTP endpoints)
    - `scripts/verify_m9_dashboard.py` (Streamlit dashboard data integration)

### D. Automated Test Suite
12. **Pytest Suite**:
    - Command: `python -m pytest tests/ -v`
    - Source: [miniproject/tests/](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/tests/) (16 test modules covering all components)

---

## 6. Discrepancies & Notable Findings

1. **Discrepancy in README command for Model Training**:
   - In [miniproject/README.md#L127](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/README.md#L127), the documentation instructs: `python -m models.train`.
   - Inspection of `miniproject/models/` reveals no `train.py` module exists in the directory.
   - The actual model training and persistence logic is implemented in [miniproject/scripts/verify_m6_experiment.py](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/scripts/verify_m6_experiment.py).
   - Furthermore, [miniproject/README.md#L130](file:///c:/Users/USER/Downloads/PD-PROJECT/miniproject/README.md#L130) cites `python scripts/verify_m6_models.py`, whereas the script on disk is named `scripts/verify_m6_experiment.py`.

2. **Anvil Binary Dependency**:
   - Anvil is required for live blockchain polling (`mempool_listener`, `transaction_generator`, and `run_final_demo.py`).
   - However, offline and mock fallbacks exist across `core/health.py`, `core/database.py`, and the pytest suite.
