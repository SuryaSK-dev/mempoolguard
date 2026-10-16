"""Deterministic Feature Extraction Engine for MempoolGuard.

Computes 30 strictly leak-free transaction, gas, historical, window, and graph
features for pending transactions using exclusively information available at or
before the transaction's first_seen_at timestamp.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import timedelta, timezone
from typing import Any

import networkx as nx

from core.contracts import FeatureVector, Transaction
from core.database import Database
from features.decoder import decode_raw_transaction

logger = logging.getLogger("mempoolguard.features")

# Canonical 30 features in deterministic, immutable order
FEATURE_NAMES: tuple[str, ...] = (
    # 1. Transaction-level features (12 features)
    "value_eth",
    "value_wei_log10",
    "gas_limit",
    "gas_price_gwei",
    "max_fee_per_gas_gwei",
    "max_priority_fee_per_gas_gwei",
    "calldata_size_bytes",
    "is_contract_call",
    "is_contract_creation",
    "function_selector_hash",
    "nonce",
    "tx_type",
    # 2. Address / History features (6 features)
    "sender_historical_tx_count",
    "receiver_historical_tx_count",
    "sender_receiver_tx_count",
    "sender_receiver_existing_relationship",
    "sender_historical_avg_value_eth",
    "sender_historical_avg_gas_limit",
    # 3. Temporal / Sliding Window features (8 features)
    "window_tx_count_60s",
    "window_tx_count_10s",
    "window_unique_receivers_60s",
    "is_burst_activity",
    "is_fan_out",
    "is_repeated_transfer",
    "window_gas_price_mean_gwei",
    "gas_price_to_window_ratio",
    # 4. Graph-Derived features (4 features)
    "sender_out_degree",
    "receiver_in_degree",
    "sender_pagerank",
    "receiver_pagerank",
)

DEFAULT_FEATURE_VERSION = "1.0.0"


class FeatureEngine:
    """Deterministic feature extractor with strict temporal anti-leakage guards."""

    def __init__(
        self,
        db: Database,
        window_seconds: float = 60.0,
        burst_window_seconds: float = 10.0,
        feature_version: str = DEFAULT_FEATURE_VERSION,
    ) -> None:
        """Initialize the FeatureEngine.

        Args:
            db: Connected Database instance containing raw and decoded transactions.
            window_seconds: Standard historical window duration in seconds (default 60s).
            burst_window_seconds: Short burst detection window in seconds (default 10s).
            feature_version: Semantic version of the feature calculation engine.
        """
        self.db = db
        self.window_seconds = window_seconds
        self.burst_window_seconds = burst_window_seconds
        self.feature_version = feature_version

    def extract_features(self, tx: Transaction) -> FeatureVector:
        """Extract all 30 deterministic features for a pending transaction.

        Guarantees:
        1. No future transactions (observed after tx.first_seen_at) are included.
        2. No post-confirmation block data is used.
        3. Stable, deterministic feature values and ordering.

        Args:
            tx: Validated Transaction model with first_seen_at set.

        Returns:
            Validated FeatureVector instance.
        """
        cutoff_time = (
            tx.first_seen_at
            if tx.first_seen_at.tzinfo
            else tx.first_seen_at.replace(tzinfo=timezone.utc)
        )
        missing_features: list[str] = []

        # ---------------------------------------------------------------------
        # 1. Transaction-Level Features (12 features)
        # ---------------------------------------------------------------------
        val_eth = round(float(tx.value_wei) / 1e18, 8)
        val_log10 = round(math.log10(float(tx.value_wei) + 1.0), 6)
        gas_limit = int(tx.gas_limit)

        # Effective gas price calculation
        if tx.gas_price_wei is not None:
            eff_gas_price = float(tx.gas_price_wei)
        elif tx.max_fee_per_gas_wei is not None:
            eff_gas_price = float(tx.max_fee_per_gas_wei)
        else:
            eff_gas_price = 0.0

        gas_price_gwei = round(eff_gas_price / 1e9, 6)
        max_fee_gwei = (
            round(float(tx.max_fee_per_gas_wei) / 1e9, 6)
            if tx.max_fee_per_gas_wei is not None
            else 0.0
        )
        max_priority_gwei = (
            round(float(tx.max_priority_fee_per_gas_wei) / 1e9, 6)
            if tx.max_priority_fee_per_gas_wei is not None
            else 0.0
        )

        # Calldata and contract interaction
        input_hex = tx.input_data.strip().lower()
        if input_hex.startswith("0x"):
            calldata_bytes = max(0, (len(input_hex) - 2) // 2)
        else:
            calldata_bytes = len(input_hex) // 2

        is_contract_call = 1 if (calldata_bytes > 0 and tx.receiver is not None) else 0
        is_contract_creation = 1 if tx.receiver is None else 0

        # Function selector representation (normalized 32-bit float)
        if calldata_bytes >= 4 and len(input_hex) >= 10:
            selector_hex = input_hex[2:10]
            try:
                selector_val = int(selector_hex, 16)
                selector_hash = round(selector_val / 4294967295.0, 6)
            except ValueError:
                selector_hash = 0.0
        else:
            selector_hash = 0.0

        nonce = int(tx.nonce)
        tx_type = 2 if tx.max_fee_per_gas_wei is not None else 0

        # ---------------------------------------------------------------------
        # 2. Address / History Features (6 features)
        # ---------------------------------------------------------------------
        # Fetch prior transactions strictly before cutoff_time (excluding current transaction)
        historical_txs = self.db.get_historical_transactions(
            cutoff_time=cutoff_time,
            exclude_tx_hash=tx.tx_hash,
        )

        sender_norm = tx.sender.strip().lower()
        receiver_norm = tx.receiver.strip().lower() if tx.receiver else None

        sender_hist = [t for t in historical_txs if t.sender == sender_norm]
        sender_hist_count = len(sender_hist)

        if sender_hist:
            sender_avg_val = round(sum(t.value_wei for t in sender_hist) / (sender_hist_count * 1e18), 8)
            sender_avg_gas = round(sum(t.gas_limit for t in sender_hist) / sender_hist_count, 2)
        else:
            sender_avg_val = 0.0
            sender_avg_gas = float(gas_limit)

        if receiver_norm is not None:
            receiver_hist = [t for t in historical_txs if t.receiver == receiver_norm]
            receiver_hist_count = len(receiver_hist)
            pair_hist = [t for t in sender_hist if t.receiver == receiver_norm]
            pair_tx_count = len(pair_hist)
            pair_relationship = 1 if pair_tx_count > 0 else 0
        else:
            receiver_hist_count = 0
            pair_tx_count = 0
            pair_relationship = 0
            missing_features.extend(
                [
                    "receiver_historical_tx_count",
                    "sender_receiver_tx_count",
                    "sender_receiver_existing_relationship",
                ]
            )

        # ---------------------------------------------------------------------
        # 3. Temporal / Sliding Window Features (8 features)
        # ---------------------------------------------------------------------
        window_start = cutoff_time - timedelta(seconds=self.window_seconds)
        burst_start = cutoff_time - timedelta(seconds=self.burst_window_seconds)

        window_txs = [t for t in historical_txs if t.first_seen_at >= window_start]
        window_sender_txs = [t for t in window_txs if t.sender == sender_norm]
        burst_sender_txs = [t for t in window_sender_txs if t.first_seen_at >= burst_start]

        win_count_60s = len(window_sender_txs)
        win_count_10s = len(burst_sender_txs)

        unique_receivers = {t.receiver for t in window_sender_txs if t.receiver is not None}
        win_unique_receivers = len(unique_receivers)

        is_burst = 1 if (win_count_10s >= 3 or win_count_60s >= 5) else 0
        is_fan_out = 1 if win_unique_receivers >= 3 else 0

        # Repeated transfer detection in window
        if receiver_norm is not None:
            repeated_found = any(
                t.receiver == receiver_norm and t.value_wei == tx.value_wei
                for t in window_sender_txs
            )
            is_repeated = 1 if repeated_found else 0
        else:
            is_repeated = 0
            missing_features.append("is_repeated_transfer")

        # Window gas price baseline and spike ratio
        if window_txs:
            gas_prices = [
                float(t.gas_price_wei or t.max_fee_per_gas_wei or 0)
                for t in window_txs
            ]
            win_gas_mean_gwei = round((sum(gas_prices) / len(gas_prices)) / 1e9, 6)
        else:
            win_gas_mean_gwei = gas_price_gwei

        gas_spike_ratio = round(gas_price_gwei / max(win_gas_mean_gwei, 0.001), 4)

        # ---------------------------------------------------------------------
        # 4. Graph-Derived Features (4 features)
        # ---------------------------------------------------------------------
        # Construct directed transaction graph from all historical transactions
        g = nx.DiGraph()
        g.add_node(sender_norm)
        if receiver_norm is not None:
            g.add_node(receiver_norm)

        for h_tx in historical_txs:
            if h_tx.receiver:
                s_addr = h_tx.sender
                r_addr = h_tx.receiver
                if g.has_edge(s_addr, r_addr):
                    g[s_addr][r_addr]["weight"] += 1
                else:
                    g.add_edge(s_addr, r_addr, weight=1)

        sender_out_degree = int(g.out_degree(sender_norm))

        if receiver_norm is not None:
            receiver_in_degree = int(g.in_degree(receiver_norm))
        else:
            receiver_in_degree = 0
            missing_features.append("receiver_in_degree")

        # Deterministic PageRank calculation
        if len(g) > 0:
            pr_scores = nx.pagerank(g, alpha=0.85, max_iter=100, tol=1e-6)
            sender_pr = round(float(pr_scores.get(sender_norm, 0.0)), 6)
            if receiver_norm is not None:
                receiver_pr = round(float(pr_scores.get(receiver_norm, 0.0)), 6)
            else:
                receiver_pr = 0.0
                missing_features.append("receiver_pagerank")
        else:
            sender_pr = 0.0
            receiver_pr = 0.0

        # ---------------------------------------------------------------------
        # Assemble Feature Dictionary (Strict Canonical Order)
        # ---------------------------------------------------------------------
        features_dict: dict[str, Any] = {
            "value_eth": val_eth,
            "value_wei_log10": val_log10,
            "gas_limit": gas_limit,
            "gas_price_gwei": gas_price_gwei,
            "max_fee_per_gas_gwei": max_fee_gwei,
            "max_priority_fee_per_gas_gwei": max_priority_gwei,
            "calldata_size_bytes": calldata_bytes,
            "is_contract_call": is_contract_call,
            "is_contract_creation": is_contract_creation,
            "function_selector_hash": selector_hash,
            "nonce": nonce,
            "tx_type": tx_type,
            "sender_historical_tx_count": sender_hist_count,
            "receiver_historical_tx_count": receiver_hist_count,
            "sender_receiver_tx_count": pair_tx_count,
            "sender_receiver_existing_relationship": pair_relationship,
            "sender_historical_avg_value_eth": sender_avg_val,
            "sender_historical_avg_gas_limit": sender_avg_gas,
            "window_tx_count_60s": win_count_60s,
            "window_tx_count_10s": win_count_10s,
            "window_unique_receivers_60s": win_unique_receivers,
            "is_burst_activity": is_burst,
            "is_fan_out": is_fan_out,
            "is_repeated_transfer": is_repeated,
            "window_gas_price_mean_gwei": win_gas_mean_gwei,
            "gas_price_to_window_ratio": gas_spike_ratio,
            "sender_out_degree": sender_out_degree,
            "receiver_in_degree": receiver_in_degree,
            "sender_pagerank": sender_pr,
            "receiver_pagerank": receiver_pr,
        }

        # Availability mask explicitly asserting pre-confirmation availability
        availability_mask = {
            "transaction": True,
            "gas": True,
            "address_history": True,
            "temporal_window": True,
            "graph": True,
            "post_confirmation": False,  # Explicitly False: no block information
        }

        return FeatureVector(
            tx_hash=tx.tx_hash.lower(),
            feature_timestamp=cutoff_time,
            features=features_dict,
            feature_names=list(FEATURE_NAMES),
            feature_version=self.feature_version,
            availability_mask=availability_mask,
            missing_features=sorted(set(missing_features)),
        )

    def extract_and_persist(self, tx: Transaction) -> FeatureVector:
        """Extract features and persist decoded transaction and feature vector to SQLite.

        Args:
            tx: Validated Transaction model.

        Returns:
            Extracted and persisted FeatureVector.
        """
        # Ensure decoded transaction is saved in database for relational consistency
        self.db.insert_decoded_transaction(tx)

        # Extract features
        fv = self.extract_features(tx)

        # Persist feature vector
        self.db.insert_feature_vector(fv)
        return fv

    def extract_from_raw_hash(self, tx_hash: str) -> FeatureVector:
        """Decode raw transaction from database by hash, extract features, and persist.

        Args:
            tx_hash: 66-character 0x-prefixed transaction hash.

        Returns:
            Extracted FeatureVector.
        """
        raw_record = self.db.get_raw_transaction(tx_hash)
        if not raw_record:
            raise KeyError(f"Transaction hash {tx_hash} not found in raw_transactions")

        tx = decode_raw_transaction(raw_record)
        return self.extract_and_persist(tx)


def main() -> None:
    """CLI runner to extract and print feature vectors for a transaction."""
    parser = argparse.ArgumentParser(description="MempoolGuard Feature Extraction Engine")
    parser.add_argument("--hash", required=True, help="Transaction hash to extract features for")
    parser.add_argument("--db", default="data/mempoolguard.db", help="Path to SQLite database")
    args = parser.parse_args()

    db = Database(args.db)
    engine = FeatureEngine(db)

    print(f"Extracting features for {args.hash}...")
    fv = engine.extract_from_raw_hash(args.hash)

    print("\nFeature Extraction Result:")
    print(f"  Tx Hash          : {fv.tx_hash}")
    print(f"  Feature Version  : {fv.feature_version}")
    print(f"  Timestamp (UTC)  : {fv.feature_timestamp.isoformat()}")
    print(f"  Feature Count    : {len(fv.features)}")
    print(f"  Missing Features : {fv.missing_features}")
    print(f"  Availability Mask: {json.dumps(fv.availability_mask)}")
    print("\nFeature Values:")
    for k, v in fv.features.items():
        print(f"  {k:<38}: {v}")


if __name__ == "__main__":
    main()
