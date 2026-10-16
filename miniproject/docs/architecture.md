# MempoolGuard System Architecture

## Architecture Overview

```mermaid
flowchart TD
    subgraph Blockchain Layer
        Anvil[Local Anvil EVM Node]
        TxGen[Synthetic Scenario Generator]
        TxGen -->|Broadcasts Tx| Anvil
    end

    subgraph Collection Layer
        Listener[Pending Transaction Listener]
        Anvil -.->|Pending Tx Event / Polling| Listener
        Decoder[Transaction Decoder]
        Listener -->|Raw Payload| DB_Raw[(Raw Storage)]
        Listener --> Decoder
        Decoder -->|Normalized Transaction| DB_Decoded[(Decoded Storage)]
    end

    subgraph Feature Layer
        Engine[Deterministic Feature Engine]
        DB_Decoded --> Engine
        Engine -->|Extract Tx, Gas, Graph Features| FV[(Feature Vectors)]
    end

    subgraph Inference & Decision
        Model[Risk Classifier XGBoost / Baselines]
        FV --> Model
        Model -->|Risk Score 0-100| DecisionEng[Intervention Decision Engine]
        DecisionEng -->|ALLOW / FLAG / HOLD| DecisionStore[(Decisions)]
        DecisionEng -->|Audit Events| AuditStore[(Audit Log)]
    end

    subgraph Serving & Visualization
        API[FastAPI REST Service]
        Dashboard[Streamlit Security Dashboard]
        DecisionStore --> API
        AuditStore --> API
        DecisionStore --> Dashboard
        AuditStore --> Dashboard
    end
```

## Architectural Components

1. **Shared Contracts (`core/contracts.py`)**:
   Pydantic models ensuring strict type safety and schema validation across transactions, feature vectors, risk results, decisions, and audit records.
2. **Configuration Loader (`core/config.py`)**:
   Tiered configuration system prioritizing environment variables over `configs/config.yaml`.
3. **Database Subsystem (`core/database.py`)**:
   SQLite database operating in WAL mode with connection management, foreign key cascades, and transactional integrity.
4. **Health Diagnostics (`core/health.py`)**:
   Self-healing and diagnostic probes testing database integrity, EVM RPC reachability, directory permissions, and runtime environment.
