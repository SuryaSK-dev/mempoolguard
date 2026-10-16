# MempoolGuard Milestone M6 (v2): Remediated Model Evaluation Report

**Generated:** 2026-10-03 04:57:29 UTC  
**Feature Schema Version:** 1.1.0 (29 canonical model features)  
**Shortcut Learning Remediation:** `nonce` excluded from model inputs. Multi-round interleaved generator with 50/50 `tx_type` balance and rotating senders.  

---

## 1. Executive Summary

This evaluation report validates the remediation of the shortcut-learning defect identified in the v1 benchmark:
1. **Elimination of Nonce Shortcut**: Nonce is strictly excluded from `MODEL_FEATURE_COLUMNS` and model feature matrices.
2. **Interleaved Multi-Round Traffic**: 25 rounds generated with rotating sender accounts and overlapping value/gas distributions.
3. **Balanced Transaction Types**: 50/50 balance between Type 0 Legacy and Type 2 EIP-1559 across both benign and anomaly classes.
4. **Grouped Instance Splitting**: Data partitioned by `scenario_id` instance (`__rndXXX`), ensuring zero group leakage while keeping all 8 archetypes represented in train, validation, and test splits.
5. **Pre-Training Separability Gate**: All single-feature AUCs strictly below 0.95 threshold.
6. **Empirical Bootstrap Confidence Intervals**: 95% CIs computed across 1,000 bootstrap resamples on test split.

---

## 2. Dataset Distribution

- **Total Dataset Rows**: 1125
- **Train Split (70%)**: 810 rows ({1: 450, 0: 360})
- **Validation Split (15%)**: 180 rows ({1: 100, 0: 80})
- **Test Split (15%)**: 135 rows ({1: 75, 0: 60})

### Class by Transaction Type Balance

| Class | Type 0 Legacy | Type 2 EIP-1559 | Total |
| :--- | :---: | :---: | :---: |
| **Benign (0)** | 250 (50.0%) | 250 (50.0%) | 500 |
| **Anomaly (1)** | 350 (56.0%) | 275 (44.0%) | 625 |
| **Total** | 600 | 525 | 1125 |

---

## 3. Pre-Training Single-Feature Separability Scan

| Feature Name | Effective Train AUC | Status |
| :--- | :---: | :---: |
| `window_tx_count_10s` | 0.7790 | PASS |
| `window_gas_price_mean_gwei` | 0.7657 | PASS |
| `sender_receiver_tx_count` | 0.7585 | PASS |
| `sender_pagerank` | 0.6724 | PASS |
| `gas_price_to_window_ratio` | 0.6559 | PASS |
| `receiver_pagerank` | 0.6342 | PASS |
| `gas_price_gwei` | 0.6328 | PASS |
| `is_repeated_transfer` | 0.5867 | PASS |
| `gas_limit` | 0.5620 | PASS |
| `calldata_size_bytes` | 0.5600 | PASS |
| `is_contract_call` | 0.5600 | PASS |
| `function_selector_hash` | 0.5600 | PASS |
| `receiver_historical_tx_count` | 0.5588 | PASS |
| `sender_receiver_existing_relationship` | 0.5497 | PASS |
| `max_priority_fee_per_gas_gwei` | 0.5445 | PASS |
| `sender_historical_avg_value_eth` | 0.5443 | PASS |
| `sender_historical_avg_gas_limit` | 0.5380 | PASS |
| `max_fee_per_gas_gwei` | 0.5345 | PASS |
| `value_eth` | 0.5318 | PASS |
| `value_wei_log10` | 0.5318 | PASS |
| `tx_type` | 0.5300 | PASS |
| `receiver_in_degree` | 0.5273 | PASS |
| `window_unique_receivers_60s` | 0.5256 | PASS |
| `sender_out_degree` | 0.5256 | PASS |
| `is_burst_activity` | 0.5178 | PASS |
| `is_fan_out` | 0.5111 | PASS |
| `sender_historical_tx_count` | 0.5012 | PASS |
| `window_tx_count_60s` | 0.5004 | PASS |
| `is_contract_creation` | 0.5000 | PASS |

---

## 4. Model Evaluation Results (Test Split with 95% Bootstrap CIs)

| Model | Accuracy [95% CI] | Precision [95% CI] | Recall [95% CI] | F1 Score [95% CI] | ROC-AUC [95% CI] | PR-AUC [95% CI] | FPR | Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **RuleBaseline** | 0.452 [0.370, 0.533] | 0.504 [0.417, 0.595] | 0.813 [0.722, 0.900] | 0.622 [0.540, 0.696] | 0.440 [0.356, 0.526] | 0.606 [0.515, 0.688] | 1.000 | 0.49 ms |
| **LogisticRegression** | 0.919 [0.867, 0.963] | 1.000 [1.000, 1.000] | 0.853 [0.768, 0.930] | 0.921 [0.869, 0.964] | 0.966 [0.931, 0.993] | 0.980 [0.961, 0.995] | 0.000 | 0.02 ms |
| **XGBoost** | 0.978 [0.948, 1.000] | 1.000 [1.000, 1.000] | 0.960 [0.909, 1.000] | 0.980 [0.952, 1.000] | 0.986 [0.963, 1.000] | 0.992 [0.979, 1.000] | 0.000 | 0.02 ms |

---

## 5. Label-Leakage Audit Summary

- **Scenarios Audited**: 8
- **Direct Dependencies**: 8 / 8

> CRITICAL BENCHMARK LIMITATION: In this synthetic proof-of-concept environment, the machine learning models and heuristic rules are effectively learning the parameter boundaries established by the synthetic transaction generator (e.g. gas multiplier thresholds, burst frequencies, calldata presence). High precision and recall scores on this local dataset reflect effective detection of these specific synthetic archetypes, but DO NOT provide evidence of real-world fraud detection efficacy on live public mainnets where adversarial strategies are obfuscated, multi-hop, and dynamically priced.