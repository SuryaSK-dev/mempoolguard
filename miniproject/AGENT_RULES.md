# MempoolGuard Agent & Development Rules

These rules govern the development, testing, and evaluation of **MempoolGuard: Machine Learning-Based Pre-Confirmation Cryptocurrency Transaction Risk Detection**.

## 1. Safety & Blockchain Environment
1. **Local Anvil Only**: All blockchain operations must target a local, isolated Anvil EVM development node (`http://127.0.0.1:8545`).
2. **No Real Funds or Keys**: Never use real cryptocurrency, private mainnet accounts, or real funds. Use only standard Anvil deterministic test accounts.
3. **No Public Broadcasts**: Transactions must never be broadcast to public networks or remote testnets.
4. **No Secrets in Source**: Do not expose or commit private keys, RPC secrets, or environment credentials.
5. **No Enforcement Claims**: `ALLOW`, `FLAG`, and `HOLD` are application-level simulated decisions. They do not alter blockchain consensus or guarantee transaction interception.

## 2. Experimental Honesty & Data Integrity
1. **Mandatory Disclaimer**: Display this disclaimer in all documentation, reports, and UI dashboards:
   > "Synthetic proof-of-concept labels; not evidence of real-world detection performance."
2. **Zero Fabrication Policy**: Never fabricate accuracy, precision, recall, F1, PR-AUC, transaction counts, latency, or benchmark results.
3. **Actual Runs Required**: All reported metrics must originate from verified execution runs with recorded sample sizes, configurations, timestamps, and commands.
4. **No Silent Substitutions**: Never substitute synthetic or fabricated values when live transactions or features are missing. Preserved missing states must be explicitly documented and handled.
5. **Report All Failures**: If a test or benchmark fails, document the exact failure and resolve the underlying issue before declaring milestones complete.

## 3. Engineering & Workflow
1. **Milestone Gating**: Complete and verify each milestone against its explicit acceptance criteria before progressing to the next.
2. **Deterministic & Replayable**: Ensure data generation, feature engineering, and model seeds are strictly deterministic and reproducible.
3. **No Temporal Leakage**: Never allow post-confirmation data or future transaction timestamps to leak into pre-confirmation feature vectors or training splits.
4. **Structured Testing**: Every component (contracts, storage, features, models, decisions, API) must have unit and integration test coverage.
