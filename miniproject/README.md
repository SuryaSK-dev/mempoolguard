# MempoolGuard

**Machine Learning-Based Pre-Confirmation Cryptocurrency Transaction Risk Detection**

> **DISCLAIMER**: Synthetic proof-of-concept labels; not evidence of real-world detection performance.
> ALLOW, FLAG, and HOLD are application-level simulated decisions. They do not modify blockchain consensus or guarantee transaction interception.

---

## 1. Project Abstract

MempoolGuard is an experimental prototype designed to detect and flag high-risk transactions before they are confirmed on-chain. Operating on a local Ethereum-compatible development environment (Anvil), MempoolGuard listens to pending transactions in the mempool, extracts transaction, gas, and behavioral graph features in real time, computes an explainable risk score using machine learning, and produces simulated intervention decisions with durable audit trails.

## 2. Technology Stack

- **Runtime**: Python 3.11+ (Active: Python 3.14.6 64-bit on Windows)
- **Local Blockchain**: Anvil (Foundry EVM)
- **Blockchain Interface**: `web3.py`
- **Persistence**: SQLite (WAL mode, transactional)
- **Data & Feature Engineering**: Pandas, NumPy, NetworkX, PyArrow
- **Machine Learning**: Scikit-Learn (baselines), XGBoost (primary classifier)
- **API & Dashboard**: FastAPI, Uvicorn, Streamlit, Plotly
- **Testing & Quality**: Pytest, Pytest-Asyncio, Ruff

## 3. Architecture & Data Pipeline

```
Pending Transaction (Local Anvil Node)
       │
       ▼
Transaction Listener & Decoder
       │
       ▼
Raw & Decoded Storage (SQLite)
       │
       ▼
Deterministic Feature Engine (Transaction, Gas, Graph-Lite)
       │
       ▼
Risk Prediction (XGBoost / Baseline)
       │
       ▼
Decision Engine (ALLOW / FLAG / HOLD)
       │
       ▼
Audit Logger (Immutable Trail)
       │
       ├──► REST API (FastAPI)
       └──► Security Dashboard (Streamlit)
```

## 4. Milestone Progress

| Milestone | Description | Status |
|-----------|-------------|--------|
| **M1** | **Foundation (Contracts, Config, Logging, SQLite Schema, Health, Tests)** | **COMPLETED & VERIFIED** |
| **M2** | **Local Blockchain and Test Traffic (Anvil, Generator, 8 Scenarios)** | **COMPLETED & VERIFIED** |
| **M3** | **Pending Transaction Listener (Mempool Observation, Dedup, Raw Storage)** | **COMPLETED & VERIFIED** |
| **M4** | **Deterministic Feature Engine (30 Canonical Features, Anti-Leakage)** | **COMPLETED & VERIFIED** |
| **M5** | **Dataset Creation and Leakage Prevention (Parquet, Splits, Proofs)** | **COMPLETED & VERIFIED** |
| **M6** | **Rules and Machine Learning (Baseline, Logistic, XGBoost, Persistence, Audit)** | **COMPLETED & VERIFIED** |
| **M7** | **Decision Engine and Audit Log (Policy, Thresholds, Safe Fallback, Relational Audit)** | **COMPLETED & VERIFIED** |
| **M8** | **FastAPI Service (Endpoints, M6/M7 Pipeline, Audit Storage, Idempotency)** | **COMPLETED & VERIFIED** |
| **M9** | **Streamlit Dashboard (Overview, Txs, Risk, Decisions, Audit, Models, Analytics, Inspector)** | **COMPLETED & VERIFIED** |
| **M10** | **Ablation Study (6 Feature Groups, 3 Model Families, Leakage Prevention, Empirical Reports)** | **COMPLETED & VERIFIED** |
| **M11** | **Latency and End-to-End Validation (Trace, Latency, Concurrency, Idempotency)** | **COMPLETED & VERIFIED** |
| **M12** | **Integration and Final Demonstration (Trace, Live Anvil, FastAPI, Dashboard)** | **COMPLETED & VERIFIED** |

## 5. Quick Start (Windows PowerShell)

### Prerequisites
- Python 3.11 or newer
- Foundry Anvil (`anvil --version`)
- Git

### Installation
```powershell
# Clone or navigate to the repository
cd d:\miniproject

# Install required dependencies
python -m pip install -r requirements.txt
```

### Starting the Local Blockchain (Milestone M2)
```powershell
# Start local isolated Anvil development node
anvil --port 8545
```

### Running Test Traffic Generation (Milestone M2)
```powershell
# Generate all 8 synthetic and benign scenarios (34 transactions)
python -m collector.transaction_generator --manifest data/raw/synthetic_scenarios.json
```

### Running Mempool Listener & Verification (Milestone M3)
```powershell
# Start live background listener
python -m collector.mempool_listener --duration 10.0

# Run complete pending-transaction lifecycle verification experiment
python scripts/verify_m3_experiment.py
```

### Running Feature Engine & Anti-Leakage Verification (Milestone M4)
```powershell
# Run deterministic feature extraction across stored transactions
python -m features.engine

# Run complete live pending capture and anti-leakage experiment
python scripts/verify_m4_experiment.py
```

### Running Dataset Builder & Anti-Leakage Verification (Milestone M5)
```powershell
# Build ML-ready dataset in Parquet and CSV format
python -m dataset.builder

# Run complete live data capture, dataset creation, splitting, and anti-leakage experiment
python scripts/verify_m5_experiment.py
```

### Running Machine Learning Pipeline & Audit (Milestone M6)
```powershell
# Train rule baseline, logistic regression, and XGBoost models
python -m models.train

# Run M6 model verification and audit checks
python scripts/verify_m6_models.py
```

### Running Decision Engine & Audit Log (Milestone M7)
```powershell
# Run genuine M6 -> M7 end-to-end policy decision and audit verification
python scripts/verify_m7_genuine_m6.py

# Run simulated threshold verification experiment
python scripts/verify_m7_experiment.py
```

### Starting FastAPI Service (Milestone M8)
```powershell
# Start FastAPI application with Uvicorn server on localhost:8000
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# Run complete live HTTP verification suite (health, predict, decision, audit, idempotency, latency)
python scripts/verify_m8_server.py
```

### Starting Streamlit Dashboard (Milestone M9)
```powershell
# Start Streamlit application on localhost:8501
python -m streamlit run dashboard/app.py --server.port 8501

# Run complete live HTTP verification suite (FastAPI + Streamlit + Real Data + Latency)
python scripts/verify_m9_dashboard.py
```

### Running Ablation Study (Milestone M10)
```powershell
# Run controlled feature ablation study across all 6 feature groups and 3 model families
python scripts/run_m10_ablation.py
```

### Running Latency and End-to-End Validation (Milestone M11)
```powershell
# Run complete local empirical latency, trace, concurrency, and reliability benchmarks
python scripts/run_m11_validation.py
```

### Running Final Integration Demonstration (Milestone M12)
```powershell
# Run complete, reproducible end-to-end integration demonstration
python scripts/run_final_demo.py
```

### Running Health Checks & Test Suite
```powershell
# Run system diagnostics
python -m core.health

# Run complete unit and integration test suite
python -m pytest tests/ -v

# Run code linter
python -m ruff check .
```

### Configuration
Configuration is managed through `configs/config.yaml`. To override settings locally without editing the YAML file, copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
