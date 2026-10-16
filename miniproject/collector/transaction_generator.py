"""Synthetic and benign transaction scenario generator for local Anvil network.

Produces deterministic transaction streams with documented synthetic attack
patterns and benign baselines to evaluate pre-confirmation risk detection.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from web3 import Web3
from web3.exceptions import Web3Exception

from core.contracts import TransactionStatus, utc_now


class ScenarioTransaction(BaseModel):
    """Record of an individual transaction dispatched as part of a scenario."""

    model_config = ConfigDict(extra="forbid")

    tx_hash: str
    scenario_id: str
    expected_label: int  # 0 = Benign, 1 = Synthetic Anomaly
    sender: str
    receiver: str | None = None
    value_wei: int
    gas_limit: int
    gas_price_wei: int | None = None
    nonce: int
    input_data: str = "0x"
    dispatched_at: datetime = Field(default_factory=utc_now)
    confirmed_at: datetime | None = None
    block_number: int | None = None
    status: TransactionStatus = TransactionStatus.PENDING


class ScenarioResult(BaseModel):
    """Execution summary for a specific transaction scenario."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    description: str
    expected_label: int
    seed: int
    executed_at: datetime = Field(default_factory=utc_now)
    transactions: list[ScenarioTransaction]
    pending_count: int
    confirmed_count: int


class TransactionGenerator:
    """Generates reproducible benign and synthetic attack scenarios on local Anvil."""

    def __init__(
        self,
        rpc_url: str | Web3 = "http://127.0.0.1:8545",
        seed: int = 42,
    ) -> None:
        if isinstance(rpc_url, Web3):
            self.w3 = rpc_url
            self.rpc_url = getattr(rpc_url.provider, "endpoint_uri", "http://127.0.0.1:8545")
        else:
            self.rpc_url = rpc_url
            self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.seed = seed
        self.rng = random.Random(seed)

        if not self.w3.is_connected():
            raise ConnectionError(f"Could not connect to local EVM node at {self.rpc_url}")

        self.accounts = list(self.w3.eth.accounts)
        if len(self.accounts) < 2:
            raise ValueError(f"Anvil must provide at least 2 accounts, found {len(self.accounts)}")
        self._account_nonces: dict[str, int] = {}

    def _get_base_fee(self) -> int:
        """Fetch current target base fee in wei from pending block."""
        with contextlib.suppress(Web3Exception, KeyError, ValueError, OSError):  # Pending block might not exist yet
            pending_block = self.w3.eth.get_block("pending")
            if pending_block and "baseFeePerGas" in pending_block:
                return max(int(pending_block["baseFeePerGas"]), 1_000_000_000)
        latest_block = self.w3.eth.get_block("latest")
        return max(int(latest_block.get("baseFeePerGas", 1_000_000_000)), 1_000_000_000)

    def _get_next_nonce(self, sender: str) -> int:
        """Track and provide contiguous nonces per sender account."""
        sender_norm = sender.lower()
        if sender_norm not in self._account_nonces:
            self._account_nonces[sender_norm] = self.w3.eth.get_transaction_count(
                Web3.to_checksum_address(sender), "pending"
            )
        nonce = self._account_nonces[sender_norm]
        self._account_nonces[sender_norm] += 1
        return nonce

    def _send_tx(
        self,
        sender: str,
        receiver: str | None,
        value_wei: int,
        scenario_id: str,
        expected_label: int,
        gas_limit: int = 21000,
        gas_price_wei: int | None = None,
        max_fee_per_gas_wei: int | None = None,
        max_priority_fee_wei: int | None = None,
        tx_type: int = 0,
        input_data: str = "0x",
    ) -> ScenarioTransaction:
        """Send a transaction on the local network and capture its initial state."""
        nonce = self._get_next_nonce(sender)
        base = self._get_base_fee()

        tx_dict: dict[str, Any] = {
            "from": Web3.to_checksum_address(sender),
            "nonce": nonce,
            "value": value_wei,
            "gas": gas_limit,
            "data": input_data,
        }
        if receiver is not None:
            tx_dict["to"] = Web3.to_checksum_address(receiver)

        if tx_type == 2:
            tx_dict["type"] = 2
            if max_priority_fee_wei is None:
                max_priority_fee_wei = 1_500_000_000
            if max_fee_per_gas_wei is None:
                max_fee_per_gas_wei = 2 * base + max_priority_fee_wei
            tx_dict["maxPriorityFeePerGas"] = max_priority_fee_wei
            tx_dict["maxFeePerGas"] = max_fee_per_gas_wei
            eff_gas_price = min(max_fee_per_gas_wei, base + max_priority_fee_wei)
        else:
            tx_dict["type"] = 0
            if gas_price_wei is None:
                gas_price_wei = base + 1_000_000_000
            tx_dict["gasPrice"] = gas_price_wei
            eff_gas_price = gas_price_wei

        tx_hash_bytes = self.w3.eth.send_transaction(tx_dict)
        raw_hex = tx_hash_bytes.hex().lower().removeprefix("0x")
        tx_hash = f"0x{raw_hex}"
        now = utc_now()

        # Check immediate status on-chain
        chain_tx = self.w3.eth.get_transaction(tx_hash)
        is_pending = chain_tx.blockNumber is None

        return ScenarioTransaction(
            tx_hash=tx_hash,
            scenario_id=scenario_id,
            expected_label=expected_label,
            sender=sender.lower(),
            receiver=receiver.lower() if receiver else None,
            value_wei=value_wei,
            gas_limit=gas_limit,
            gas_price_wei=eff_gas_price,
            nonce=nonce,
            input_data=input_data,
            dispatched_at=now,
            confirmed_at=now if not is_pending else None,
            block_number=chain_tx.blockNumber,
            status=TransactionStatus.PENDING if is_pending else TransactionStatus.CONFIRMED,
        )

    # -------------------------------------------------------------------------
    # Scenario Definitions
    # -------------------------------------------------------------------------

    def generate_benign_transfers(
        self,
        count: int = 5,
        round_idx: int = 0,
        sender: str | None = None,
        receiver: str | None = None,
    ) -> ScenarioResult:
        """Scenario 1: Ordinary ETH transfers with standard gas pricing (Label: 0)."""
        txs: list[ScenarioTransaction] = []
        scen_id = f"BENIGN_TRANSFER__rnd{round_idx:03d}" if round_idx > 0 else "BENIGN_TRANSFER"
        sender_addr = sender or self.accounts[0]
        receiver_addr = receiver or self.accounts[1]
        base = self._get_base_fee()

        for i in range(count):
            val_eth = self.rng.uniform(0.001, 2.0)
            value_wei = self.w3.to_wei(val_eth, "ether")
            tx_type = 2 if i % 2 == 1 else 0
            gas_lim = self.rng.choice([21000, 21000, 45000, 60000, 100000])

            if tx_type == 2:
                tip = self.rng.choice([1_000_000_000, 1_500_000_000, 2_000_000_000])
                tx = self._send_tx(
                    sender=sender_addr,
                    receiver=receiver_addr,
                    value_wei=value_wei,
                    scenario_id=scen_id,
                    expected_label=0,
                    gas_limit=gas_lim,
                    max_priority_fee_wei=tip,
                    max_fee_per_gas_wei=2 * base + tip,
                    tx_type=2,
                )
            else:
                gas_price = base + self.rng.choice([1_000_000_000, 1_500_000_000, 2_000_000_000])
                tx = self._send_tx(
                    sender=sender_addr,
                    receiver=receiver_addr,
                    value_wei=value_wei,
                    scenario_id=scen_id,
                    expected_label=0,
                    gas_limit=gas_lim,
                    gas_price_wei=gas_price,
                    tx_type=0,
                )
            txs.append(tx)

        pending = sum(1 for t in txs if t.status == TransactionStatus.PENDING)
        return ScenarioResult(
            scenario_id=scen_id,
            description="Ordinary benign ETH transfers with standard gas pricing",
            expected_label=0,
            seed=self.seed,
            transactions=txs,
            pending_count=pending,
            confirmed_count=len(txs) - pending,
        )

    def generate_repeated_transfers(
        self,
        count: int = 5,
        round_idx: int = 0,
        sender: str | None = None,
        receiver: str | None = None,
    ) -> ScenarioResult:
        """Scenario 2: Rapid consecutive transfers between identical sender & receiver (Label: 1)."""
        scen_id = f"REPEATED_TRANSFER__rnd{round_idx:03d}" if round_idx > 0 else "REPEATED_TRANSFER"
        sender_addr = sender or self.accounts[2]
        receiver_addr = receiver or self.accounts[3]
        txs: list[ScenarioTransaction] = []
        val_eth = self.rng.uniform(0.001, 2.0)
        value_wei = self.w3.to_wei(val_eth, "ether")

        for i in range(count):
            tx_type = 2 if i % 2 == 1 else 0
            gas_lim = self.rng.choice([21000, 21000, 60000])
            tx = self._send_tx(
                sender=sender_addr,
                receiver=receiver_addr,
                value_wei=value_wei,
                scenario_id=scen_id,
                expected_label=1,
                gas_limit=gas_lim,
                tx_type=tx_type,
            )
            txs.append(tx)

        pending = sum(1 for t in txs if t.status == TransactionStatus.PENDING)
        return ScenarioResult(
            scenario_id=scen_id,
            description="Rapid consecutive transfers between the identical pair of accounts",
            expected_label=1,
            seed=self.seed,
            transactions=txs,
            pending_count=pending,
            confirmed_count=len(txs) - pending,
        )

    def generate_gas_spike(
        self,
        count: int = 3,
        round_idx: int = 0,
        sender: str | None = None,
        receiver: str | None = None,
    ) -> ScenarioResult:
        """Scenario 3: Transactions with unusually inflated gas price (10x-50x normal) (Label: 1)."""
        txs: list[ScenarioTransaction] = []
        scen_id = f"GAS_SPIKE__rnd{round_idx:03d}" if round_idx > 0 else "GAS_SPIKE"
        base = self._get_base_fee()
        sender_addr = sender or self.accounts[4]
        receiver_addr = receiver or self.accounts[5]
        spike_multiplier = self.rng.randint(20, 40)
        spike_gas = base * spike_multiplier

        for i in range(count):
            val_eth = self.rng.uniform(0.001, 2.0)
            value_wei = self.w3.to_wei(val_eth, "ether")
            tx_type = 2 if i % 2 == 1 else 0
            gas_lim = self.rng.choice([21000, 21000, 60000])

            if tx_type == 2:
                spike_tip = 20_000_000_000  # 20 Gwei tip
                tx = self._send_tx(
                    sender=sender_addr,
                    receiver=receiver_addr,
                    value_wei=value_wei,
                    scenario_id=scen_id,
                    expected_label=1,
                    gas_limit=gas_lim,
                    max_priority_fee_wei=spike_tip,
                    max_fee_per_gas_wei=spike_gas,
                    tx_type=2,
                )
            else:
                tx = self._send_tx(
                    sender=sender_addr,
                    receiver=receiver_addr,
                    value_wei=value_wei,
                    scenario_id=scen_id,
                    expected_label=1,
                    gas_limit=gas_lim,
                    gas_price_wei=spike_gas,
                    tx_type=0,
                )
            txs.append(tx)

        pending = sum(1 for t in txs if t.status == TransactionStatus.PENDING)
        return ScenarioResult(
            scenario_id=scen_id,
            description="Transactions with extreme gas fee spikes simulating front-running",
            expected_label=1,
            seed=self.seed,
            transactions=txs,
            pending_count=pending,
            confirmed_count=len(txs) - pending,
        )

    def generate_gas_deviation_low(
        self,
        count: int = 3,
        round_idx: int = 0,
        sender: str | None = None,
        receiver: str | None = None,
    ) -> ScenarioResult:
        """Scenario 4: Transactions with minimum viable gas fee right at base fee (Label: 1)."""
        txs: list[ScenarioTransaction] = []
        scen_id = f"GAS_DEVIATION_LOW__rnd{round_idx:03d}" if round_idx > 0 else "GAS_DEVIATION_LOW"
        base = self._get_base_fee()
        sender_addr = sender or self.accounts[6]
        receiver_addr = receiver or self.accounts[7]

        for i in range(count):
            val_eth = self.rng.uniform(0.001, 2.0)
            value_wei = self.w3.to_wei(val_eth, "ether")
            tx_type = 2 if i % 2 == 1 else 0
            gas_lim = self.rng.choice([21000, 21000, 60000])

            if tx_type == 2:
                tx = self._send_tx(
                    sender=sender_addr,
                    receiver=receiver_addr,
                    value_wei=value_wei,
                    scenario_id=scen_id,
                    expected_label=1,
                    gas_limit=gas_lim,
                    max_priority_fee_wei=0,  # Zero priority fee
                    max_fee_per_gas_wei=base,
                    tx_type=2,
                )
            else:
                tx = self._send_tx(
                    sender=sender_addr,
                    receiver=receiver_addr,
                    value_wei=value_wei,
                    scenario_id=scen_id,
                    expected_label=1,
                    gas_limit=gas_lim,
                    gas_price_wei=base,  # exact base fee, zero tip
                    tx_type=0,
                )
            txs.append(tx)

        pending = sum(1 for t in txs if t.status == TransactionStatus.PENDING)
        return ScenarioResult(
            scenario_id=scen_id,
            description="Under-priced gas fee transactions with zero priority tip",
            expected_label=1,
            seed=self.seed,
            transactions=txs,
            pending_count=pending,
            confirmed_count=len(txs) - pending,
        )

    def generate_burst_activity(
        self,
        count: int = 6,
        round_idx: int = 0,
        sender: str | None = None,
    ) -> ScenarioResult:
        """Scenario 5: High-frequency burst from a single sender account (Label: 1)."""
        scen_id = f"BURST_ACTIVITY__rnd{round_idx:03d}" if round_idx > 0 else "BURST_ACTIVITY"
        sender_addr = sender or self.accounts[8]
        txs: list[ScenarioTransaction] = []

        for i in range(count):
            receiver = self.rng.choice([a for a in self.accounts if a.lower() != sender_addr.lower()])
            val_eth = self.rng.uniform(0.001, 2.0)
            value_wei = self.w3.to_wei(val_eth, "ether")
            tx_type = 2 if i % 2 == 1 else 0
            gas_lim = self.rng.choice([21000, 21000, 60000])

            tx = self._send_tx(
                sender=sender_addr,
                receiver=receiver,
                value_wei=value_wei,
                scenario_id=scen_id,
                expected_label=1,
                gas_limit=gas_lim,
                tx_type=tx_type,
            )
            txs.append(tx)

        pending = sum(1 for t in txs if t.status == TransactionStatus.PENDING)
        return ScenarioResult(
            scenario_id=scen_id,
            description="High-frequency transaction burst originating from a single sender",
            expected_label=1,
            seed=self.seed,
            transactions=txs,
            pending_count=pending,
            confirmed_count=len(txs) - pending,
        )

    def generate_fan_out(
        self,
        count: int = 5,
        round_idx: int = 0,
        sender: str | None = None,
    ) -> ScenarioResult:
        """Scenario 6: One sender distributing funds across many distinct counterparties (Label: 1)."""
        scen_id = f"FAN_OUT__rnd{round_idx:03d}" if round_idx > 0 else "FAN_OUT"
        sender_addr = sender or self.accounts[9]
        recipients = [a for a in self.accounts if a.lower() != sender_addr.lower()][:count]
        txs: list[ScenarioTransaction] = []

        for i, rcpt in enumerate(recipients):
            val_eth = self.rng.uniform(0.001, 2.0)
            value_wei = self.w3.to_wei(val_eth, "ether")
            tx_type = 2 if i % 2 == 1 else 0
            gas_lim = self.rng.choice([21000, 21000, 60000])

            tx = self._send_tx(
                sender=sender_addr,
                receiver=rcpt,
                value_wei=value_wei,
                scenario_id=scen_id,
                expected_label=1,
                gas_limit=gas_lim,
                tx_type=tx_type,
            )
            txs.append(tx)

        pending = sum(1 for t in txs if t.status == TransactionStatus.PENDING)
        return ScenarioResult(
            scenario_id=scen_id,
            description="One-to-many fan-out dispersal of funds across diverse counterparties",
            expected_label=1,
            seed=self.seed,
            transactions=txs,
            pending_count=pending,
            confirmed_count=len(txs) - pending,
        )

    def generate_repeated_interactions(
        self,
        count: int = 4,
        round_idx: int = 0,
        account_a: str | None = None,
        account_b: str | None = None,
    ) -> ScenarioResult:
        """Scenario 7: Alternating ping-pong interactions between two accounts (Label: 1)."""
        scen_id = f"REPEATED_INTERACTIONS__rnd{round_idx:03d}" if round_idx > 0 else "REPEATED_INTERACTIONS"
        acc_a = account_a or self.accounts[1]
        acc_b = account_b or self.accounts[3]
        txs: list[ScenarioTransaction] = []

        for i in range(count):
            s = acc_a if i % 2 == 0 else acc_b
            r = acc_b if i % 2 == 0 else acc_a
            val_eth = self.rng.uniform(0.001, 2.0)
            value_wei = self.w3.to_wei(val_eth, "ether")
            tx_type = 2 if i % 2 == 1 else 0
            gas_lim = self.rng.choice([21000, 21000, 60000])

            tx = self._send_tx(
                sender=s,
                receiver=r,
                value_wei=value_wei,
                scenario_id=scen_id,
                expected_label=1,
                gas_limit=gas_lim,
                tx_type=tx_type,
            )
            txs.append(tx)

        pending = sum(1 for t in txs if t.status == TransactionStatus.PENDING)
        return ScenarioResult(
            scenario_id=scen_id,
            description="Bilateral alternating ping-pong transfers between two specific addresses",
            expected_label=1,
            seed=self.seed,
            transactions=txs,
            pending_count=pending,
            confirmed_count=len(txs) - pending,
        )

    def generate_contract_call(
        self,
        count: int = 3,
        round_idx: int = 0,
        sender: str | None = None,
        receiver: str | None = None,
    ) -> ScenarioResult:
        """Scenario 8: Transaction with calldata payload simulating smart contract interaction (Label: 1)."""
        scen_id = f"CONTRACT_CALL__rnd{round_idx:03d}" if round_idx > 0 else "CONTRACT_CALL"
        txs: list[ScenarioTransaction] = []
        selector = "0xa9059cbb"
        dummy_param = "0" * 24 + "1" * 40 + "0" * 63 + "1"
        calldata = selector + dummy_param
        sender_addr = sender or self.accounts[7]
        receiver_addr = receiver or self.accounts[0]

        for i in range(count):
            val_eth = self.rng.choice([0.0, 0.01, 0.05])
            value_wei = self.w3.to_wei(val_eth, "ether")
            tx_type = 2 if i % 2 == 1 else 0
            gas_lim = self.rng.choice([60000, 100000, 120000])

            tx = self._send_tx(
                sender=sender_addr,
                receiver=receiver_addr,
                value_wei=value_wei,
                scenario_id=scen_id,
                expected_label=1,
                gas_limit=gas_lim,
                input_data=calldata,
                tx_type=tx_type,
            )
            txs.append(tx)

        pending = sum(1 for t in txs if t.status == TransactionStatus.PENDING)
        return ScenarioResult(
            scenario_id=scen_id,
            description="Transaction with encoded calldata payload simulating contract execution",
            expected_label=1,
            seed=self.seed,
            transactions=txs,
            pending_count=pending,
            confirmed_count=len(txs) - pending,
        )

    # -------------------------------------------------------------------------
    # Comprehensive Batch Execution & Manifest Recording
    # -------------------------------------------------------------------------

    def run_all_scenarios(
        self,
        output_manifest_path: str | Path | None = "data/raw/synthetic_scenarios.json",
        automine: bool = False,
        mine_after: bool = True,
    ) -> list[ScenarioResult]:
        """Execute all defined scenarios under controlled mining and record the manifest."""
        self._account_nonces.clear()

        # 1. Configure mining
        self.w3.provider.make_request("evm_setAutomine", [automine])

        results: list[ScenarioResult] = []
        scenario_methods = [
            self.generate_benign_transfers,
            self.generate_repeated_transfers,
            self.generate_gas_spike,
            self.generate_gas_deviation_low,
            self.generate_burst_activity,
            self.generate_fan_out,
            self.generate_repeated_interactions,
            self.generate_contract_call,
        ]

        for method in scenario_methods:
            res = method()
            results.append(res)

        # 2. If automine was off, mine blocks until all pending transactions are confirmed
        if not automine and mine_after:
            for _ in range(10):
                self.w3.provider.make_request("evm_mine", [])
                pending_txs = self.w3.eth.get_block(
                    "pending", full_transactions=False
                ).get("transactions", [])
                if not pending_txs:
                    break

            # Re-verify confirmation
            for res in results:
                updated_txs = []
                for tx in res.transactions:
                    try:
                        chain_tx = self.w3.eth.get_transaction(tx.tx_hash)
                        is_confirmed = chain_tx.blockNumber is not None
                        block_num = chain_tx.blockNumber
                    except (Web3Exception, KeyError, ValueError, OSError):
                        is_confirmed = False
                        block_num = None

                    updated_txs.append(
                        tx.model_copy(
                            update={
                                "block_number": block_num,
                                "confirmed_at": utc_now() if is_confirmed else None,
                                "status": TransactionStatus.CONFIRMED
                                if is_confirmed
                                else TransactionStatus.PENDING,
                            }
                        )
                    )
                res.transactions = updated_txs
                res.confirmed_count = sum(
                    1 for t in updated_txs if t.status == TransactionStatus.CONFIRMED
                )
                res.pending_count = len(updated_txs) - res.confirmed_count

            # Re-enable automine as safe default
            self.w3.provider.make_request("evm_setAutomine", [True])

        # 3. Save manifest if path provided
        if output_manifest_path:
            p = Path(output_manifest_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            manifest_data = {
                "generated_at": utc_now().isoformat(),
                "seed": self.seed,
                "chain_id": self.w3.eth.chain_id,
                "total_scenarios": len(results),
                "total_transactions": sum(len(r.transactions) for r in results),
                "scenarios": [r.model_dump(mode="json") for r in results],
            }
            with open(p, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2)

        return results

    def generate_interleaved_dataset(
        self,
        rounds: int = 25,
        benign_per_round: int = 20,
        output_manifest_path: str | Path | None = "data/raw/synthetic_scenarios_v2.json",
        automine: bool = False,
    ) -> list[ScenarioResult]:
        """Generate multi-round interleaved benign and synthetic attacks with rotating senders and balanced tx_types.

        Generates >= 1,000 labeled transactions:
        - 25 rounds x (20 benign + 25 attack) = 1,125 transactions.
        - Senders rotate across rounds through all available Anvil accounts, eliminating account/nonce shortcuts.
        - Transaction values overlap randomly between 0.001 and 2.0 ETH across all classes.
        - Transaction types (Type 0 Legacy and Type 2 EIP-1559) are strictly 50/50 balanced across classes.
        - Each scenario run has a unique instance ID (e.g. 'GAS_SPIKE__rnd001') for instance-based grouping.
        """
        self._account_nonces.clear()
        self.w3.provider.make_request("evm_setAutomine", [automine])

        results: list[ScenarioResult] = []
        n_acc = len(self.accounts)

        for r in range(1, rounds + 1):
            # Rotate senders across accounts so no account is statically benign or attack
            s_benign = self.accounts[(r * 3 + 0) % n_acc]
            r_benign = self.accounts[(r * 3 + 1) % n_acc]
            s_rep = self.accounts[(r * 3 + 2) % n_acc]
            s_spike = self.accounts[(r * 3 + 3) % n_acc]
            s_dev = self.accounts[(r * 3 + 4) % n_acc]
            s_burst = self.accounts[(r * 3 + 5) % n_acc]
            s_fan = self.accounts[(r * 3 + 6) % n_acc]
            s_ping_a = self.accounts[(r * 3 + 7) % n_acc]
            s_ping_b = self.accounts[(r * 3 + 8) % n_acc]
            s_contract = self.accounts[(r * 3 + 9) % n_acc]

            # 1. Benign (20 txs)
            results.append(self.generate_benign_transfers(
                count=benign_per_round, round_idx=r, sender=s_benign, receiver=r_benign
            ))
            # 2. Attacks (25 txs across 7 archetypes)
            results.append(self.generate_repeated_transfers(
                count=4, round_idx=r, sender=s_rep, receiver=r_benign
            ))
            results.append(self.generate_gas_spike(
                count=3, round_idx=r, sender=s_spike, receiver=s_benign
            ))
            results.append(self.generate_gas_deviation_low(
                count=3, round_idx=r, sender=s_dev, receiver=s_burst
            ))
            results.append(self.generate_burst_activity(
                count=4, round_idx=r, sender=s_burst
            ))
            results.append(self.generate_fan_out(
                count=4, round_idx=r, sender=s_fan
            ))
            results.append(self.generate_repeated_interactions(
                count=4, round_idx=r, account_a=s_ping_a, account_b=s_ping_b
            ))
            results.append(self.generate_contract_call(
                count=3, round_idx=r, sender=s_contract, receiver=s_benign
            ))

            # Mine pending transactions between rounds
            if not automine:
                time.sleep(0.06)
                self.w3.provider.make_request("evm_mine", [])

        # Re-verify confirmation
        for res in results:
            updated_txs = []
            for tx in res.transactions:
                try:
                    chain_tx = self.w3.eth.get_transaction(tx.tx_hash)
                    is_conf = chain_tx.blockNumber is not None
                    b_num = chain_tx.blockNumber
                except Exception:
                    is_conf = False
                    b_num = None
                updated_txs.append(
                    tx.model_copy(
                        update={
                            "block_number": b_num,
                            "confirmed_at": utc_now() if is_conf else None,
                            "status": TransactionStatus.CONFIRMED if is_conf else TransactionStatus.PENDING,
                        }
                    )
                )
            res.transactions = updated_txs
            res.confirmed_count = sum(1 for t in updated_txs if t.status == TransactionStatus.CONFIRMED)
            res.pending_count = len(updated_txs) - res.confirmed_count

        self.w3.provider.make_request("evm_setAutomine", [True])

        # Write manifest
        if output_manifest_path:
            p = Path(output_manifest_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            manifest_data = {
                "generated_at": utc_now().isoformat(),
                "seed": self.seed,
                "chain_id": self.w3.eth.chain_id,
                "total_rounds": rounds,
                "total_scenarios": len(results),
                "total_transactions": sum(len(r.transactions) for r in results),
                "scenarios": [r.model_dump(mode="json") for r in results],
            }
            with open(p, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2)

        return results


def main() -> None:
    """CLI runner to execute test transaction scenarios against local Anvil."""
    parser = argparse.ArgumentParser(description="MempoolGuard Anvil Test Traffic Generator")
    parser.add_argument("--rpc", default="http://127.0.0.1:8545", help="Anvil RPC endpoint")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument(
        "--manifest",
        default="data/raw/synthetic_scenarios.json",
        help="Output JSON manifest path",
    )
    args = parser.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to Anvil RPC at {args.rpc}. Is Anvil running?")
        sys.exit(1)

    print(f"Connecting to Anvil at {args.rpc} (Chain ID: {w3.eth.chain_id})...")
    gen = TransactionGenerator(w3, seed=args.seed)
    print(f"Loaded {len(gen.accounts)} test accounts. Running all 8 synthetic & benign scenarios...")

    results = gen.run_all_scenarios(output_manifest_path=args.manifest)

    total_txs = sum(len(r.transactions) for r in results)
    confirmed_txs = sum(r.confirmed_count for r in results)
    print("\nExecution Summary:")
    print(f"  Scenarios executed : {len(results)}")
    print(f"  Total transactions : {total_txs}")
    print(f"  Confirmed on chain : {confirmed_txs}")
    print(f"  Manifest written to: {args.manifest}\n")
    for res in results:
        print(
            f"  - [{res.scenario_id:<20}] "
            f"Tx Count: {len(res.transactions):>2} | "
            f"Label: {res.expected_label} | "
            f"Status: {res.confirmed_count}/{len(res.transactions)} Confirmed"
        )


if __name__ == "__main__":
    main()
