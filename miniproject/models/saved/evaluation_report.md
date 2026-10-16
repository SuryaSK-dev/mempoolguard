# MempoolGuard Milestone M6: Model Evaluation Report

**Generated:** 2026-09-30 11:02:05 UTC  
**Feature Version:** 1.0.0 (30 canonical features)  
**Evaluator:** Strict anti-leakage evaluator with single-class guardrails.

---

## 1. Executive Summary

Milestone M6 implements, persists, and rigorously evaluates three distinct risk-scoring approaches for pre-confirmation EVM transactions:
1. **Rule-Based Baseline (`RuleBaseline`)**: Heuristic checks with 0–100 continuous score and granular reason codes.
2. **Logistic Regression (`LogisticRegressionModel`)**: Linear probability model with train-only median imputation and standard scaling, balanced class weights, and validation-tuned decision threshold.
3. **XGBoost Classifier (`XGBoostModel`)**: Non-linear gradient boosted trees (`hist` method) with exact 30-feature schema enforcement and validation threshold optimization.

---

## 2. Evaluation A: Existing M5 Chronological Dataset (34 Rows)

### Dataset Distribution
- **Total Rows**: 34
- **Train Split (70%)**: 23 rows (18 Anomaly, 5 Benign)
- **Validation Split (15%)**: 5 rows (5 Anomaly, 0 Benign)
- **Test Split (15%)**: 6 rows (6 Anomaly, 0 Benign)

> [!WARNING]
> **Single-Class Test Split Limitation**: Because benign transactions were executed first in M5, chronological splitting placed all 5 benign samples into `train`. Both `val` and `test` contain exclusively class 1 (anomaly).
> Consequently:
> - **False Positive Rate (FPR)** is mathematically undefined ($0 / (0 + 0)$) and reported as `N/A`.
> - **ROC-AUC** is mathematically undefined on a single-class split and reported as `N/A`.
> - **PR-AUC** is undefined without negative samples and reported as `N/A`.
> - Metrics reflect positive-class recall and precision without negative sample discrimination.

### Evaluation A Results Table

| Model | Test Rows | Class Dist (0/1) | Precision | Recall | F1 Score | ROC-AUC | PR-AUC | FPR | Inference Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Rule Baseline** | 6 | 0 / 6 | 0.000 | 0.000 | 0.000 | N/A* | N/A* | N/A* | 0.075 ms |
| **Logistic Regression** | 6 | 0 / 6 | 1.000 | 0.833 | 0.909 | N/A* | N/A* | N/A* | 0.110 ms |
| **XGBoost** | 6 | 0 / 6 | 1.000 | 0.833 | 0.909 | N/A* | N/A* | N/A* | 0.254 ms |

*\*Reported as N/A due to single-class test split (no negative samples).*

---

## 3. Evaluation B: Balanced / Expanded Benchmark Dataset

To demonstrate model discrimination ability without fabricating data, an independent, multi-round experiment was conducted on local Anvil (seed=100). Benign and synthetic attack traffic were interleaved chronologically across 3 sequential rounds.

### Expanded Dataset Distribution
- **Total Transactions Captured**: 47
- **Train Split (70%)**: 32 rows ({1: 22, 0: 10})
- **Validation Split (15%)**: 7 rows ({0: 4, 1: 3})
- **Test Split (15%)**: 8 rows ({1: 4, 0: 4})

Both classes are present in all three chronological splits, enabling statistically valid computation of ROC-AUC, PR-AUC, FPR, FNR, and Confusion Matrices.

### Evaluation B Results Table

| Model | Test Rows | Class Dist (0/1) | Accuracy | Precision | Recall | F1 Score | ROC-AUC | PR-AUC | FPR | Confusion Matrix [TN, FP; FN, TP] | Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Rule Baseline** | 8 | 4 / 4 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.417 | 1.000 | [[0, 4], [4, 0]] | 0.065 ms |
| **Logistic Regression** | 8 | 4 / 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | [[4, 0], [0, 4]] | 0.086 ms |
| **XGBoost** | 8 | 4 / 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | [[4, 0], [0, 4]] | 0.219 ms |

---

## 4. Label-Leakage Audit Subsystem

### Findings
- **Scenarios Audited**: 8 / 8
- **Direct Generator-to-Feature Dependencies**: 8 / 8

| Scenario ID | Expected Label | Generator Behavior | Correlated Pre-Confirmation Features | Leakage Level |
| :--- | :---: | :--- | :--- | :---: |
| `BENIGN_TRANSFER` | 0 | Baseline ETH transfer with base fee | `gas_price_to_window_ratio`, `is_burst_activity`, `is_fan_out` | HIGH |
| `REPEATED_TRANSFER` | 1 | Rapid transfers between identical pair | `is_repeated_transfer`, `sender_receiver_tx_count` | HIGH |
| `GAS_SPIKE` | 1 | Gas price set to 25x base fee | `gas_price_gwei`, `gas_price_to_window_ratio` | HIGH |
| `GAS_DEVIATION_LOW` | 1 | Sub-base fee or zero priority | `gas_price_gwei`, `gas_price_to_window_ratio` | MEDIUM |
| `BURST_ACTIVITY` | 1 | 6 transfers dispatched in seconds | `window_tx_count_10s`, `window_tx_count_60s`, `is_burst_activity` | HIGH |
| `FAN_OUT` | 1 | Transfers to 5 distinct addresses in 60s | `window_unique_receivers_60s`, `is_fan_out`, `sender_out_degree` | HIGH |
| `REPEATED_INTERACTIONS` | 1 | Cyclic interactions alternating pair | `sender_receiver_tx_count`, `sender_receiver_existing_relationship` | MEDIUM |
| `CONTRACT_CALL` | 1 | Non-empty bytecode payload | `is_contract_call`, `calldata_size_bytes`, `function_selector_hash` | HIGH |

### Critical Benchmark Limitation Statement
> [!CAUTION]
> **CRITICAL BENCHMARK LIMITATION**: In this synthetic proof-of-concept environment, the machine learning models and heuristic rules are effectively learning the parameter boundaries established by the synthetic transaction generator (e.g. gas multiplier thresholds, burst frequencies, calldata presence). High precision and recall scores on this local dataset reflect effective detection of these specific synthetic archetypes, but **DO NOT** provide evidence of real-world fraud detection efficacy on live public mainnets where adversarial strategies are obfuscated, multi-hop, and dynamically priced.

---

## 5. Model Artifacts and Verification

Persisted model artifacts are saved in `models/saved/`:
- `RuleBaseline`: `baseline_metadata.json`
- `LogisticRegression`: `logistic_regression.joblib` + `logistic_regression_metadata.json`
- `XGBoost`: `xgboost_model.joblib` + `xgboost_model_metadata.json`

Every saved artifact includes:
1. Model semantic version (`1.0.0`).
2. Exact feature schema version (`1.0.0`).
3. Complete 30 canonical feature names in strict order.
4. Validation threshold and training hyperparameters.
5. Verification that attempting to load a model with an mismatched feature version raises an explicit `ValueError`.
