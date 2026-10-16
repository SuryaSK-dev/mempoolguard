# MempoolGuard — Known Limitations & PoC Scope

> **Mandatory Disclaimer**: Synthetic proof-of-concept labels; not evidence of real-world detection performance.

## 1. Simulated Application Decisions vs. Blockchain Consensus
- `ALLOW`, `FLAG`, and `HOLD` are application-level simulated decisions.
- They do not alter decentralized blockchain consensus, manipulate miner/validator ordering directly, or guarantee transaction cancellation or interception on a public blockchain.

## 2. Synthetic Evaluation Labels
- Labels used to evaluate detection models in this prototype are generated via controlled synthetic scenarios (e.g. burst transfers, gas price spikes, repetitive counterparty pinging).
- These synthetic patterns serve as proof-of-concept evaluations and must not be interpreted as evidence of generalized detection efficacy against sophisticated real-world adversaries.

## 3. Local Single-Node Environment
- In a single-node local EVM environment (such as Anvil), multi-node peer-to-peer transaction propagation dynamics do not exist.
- Propagation-based feature groups (e.g. peer relay counts, propagation latency across geographic nodes) are explicitly marked as unavailable in the feature availability mask (`propagation: false`).

## 4. Execution Timing & Anvil Auto-Mining
- In default configurations, local development nodes may mine transactions instantly upon submission.
- Real pre-confirmation observation requires configuring Anvil with explicit interval mining or delayed block production so that transactions remain in the mempool long enough for extraction and inference.
