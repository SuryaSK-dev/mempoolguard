"""Unit and integration tests for Anvil local blockchain and transaction generator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from web3 import Web3

from collector.transaction_generator import TransactionGenerator
from core.anvil import AnvilController
from core.contracts import TransactionStatus


@pytest.fixture
def generator(anvil_controller: AnvilController) -> TransactionGenerator:
    """Fixture providing initialized TransactionGenerator."""
    return TransactionGenerator(rpc_url=anvil_controller.rpc_http_url, seed=42)


def test_anvil_connectivity(anvil_controller: AnvilController, w3: Web3) -> None:
    """Verify local Anvil node connectivity, client version, and chain ID."""
    assert anvil_controller.is_running() is True
    assert w3.is_connected() is True
    assert w3.eth.chain_id == 31337
    client_ver = anvil_controller.get_client_version()
    assert "anvil" in client_ver.lower()


def test_anvil_local_accounts(anvil_controller: AnvilController, w3: Web3) -> None:
    """Verify local development accounts are available with funded balances."""
    accounts = anvil_controller.get_accounts()
    assert len(accounts) >= 10
    # Anvil accounts are funded with 10,000 test ETH by default
    balance_wei = w3.eth.get_balance(accounts[0])
    assert balance_wei > 0
    assert w3.from_wei(balance_wei, "ether") >= 100


def test_pending_transaction_lifecycle(
    anvil_controller: AnvilController, generator: TransactionGenerator, w3: Web3
) -> None:
    """Verify transactions remain pending when automine is disabled, then confirm upon mining."""
    # 1. Disable automine
    assert anvil_controller.set_automine(False) is True

    # 2. Dispatch transaction
    sender = generator.accounts[0]
    receiver = generator.accounts[1]
    res = generator._send_tx(
        sender=sender,
        receiver=receiver,
        value_wei=w3.to_wei(0.1, "ether"),
        scenario_id="TEST_PENDING",
        expected_label=0,
    )

    # 3. Verify transaction is PENDING on-chain
    assert res.status == TransactionStatus.PENDING
    assert res.block_number is None

    chain_tx = w3.eth.get_transaction(res.tx_hash)
    assert chain_tx.blockNumber is None

    # 4. Explicitly mine block
    mined_block = anvil_controller.mine_blocks(1)
    assert mined_block > 0

    # 5. Verify transaction transitioned to terminal CONFIRMED state
    confirmed_tx = w3.eth.get_transaction(res.tx_hash)
    receipt = w3.eth.get_transaction_receipt(res.tx_hash)
    assert confirmed_tx.blockNumber is not None
    assert receipt.status == 1

    # Re-enable automine
    anvil_controller.set_automine(True)


def test_benign_transfer_generation(generator: TransactionGenerator) -> None:
    """Test generating standard benign ETH transfers."""
    result = generator.generate_benign_transfers(count=3)
    assert result.scenario_id == "BENIGN_TRANSFER"
    assert result.expected_label == 0
    assert len(result.transactions) == 3
    for tx in result.transactions:
        assert tx.expected_label == 0
        assert tx.tx_hash.startswith("0x")
        assert tx.value_wei > 0


def test_gas_spike_generation(generator: TransactionGenerator) -> None:
    """Test generating gas fee anomaly scenario."""
    base_fee = generator._get_base_fee()
    result = generator.generate_gas_spike(count=2)
    assert result.scenario_id == "GAS_SPIKE"
    assert result.expected_label == 1
    assert len(result.transactions) == 2
    for tx in result.transactions:
        assert tx.gas_price_wei is not None
        assert tx.gas_price_wei > base_fee * 10


def test_burst_activity_generation(generator: TransactionGenerator) -> None:
    """Test generating high-frequency burst from single sender."""
    result = generator.generate_burst_activity(count=4)
    assert result.scenario_id == "BURST_ACTIVITY"
    assert result.expected_label == 1
    assert len(result.transactions) == 4
    senders = {tx.sender for tx in result.transactions}
    assert len(senders) == 1  # All from the same sender


def test_fan_out_generation(generator: TransactionGenerator) -> None:
    """Test generating fan-out dispersal to multiple recipients."""
    result = generator.generate_fan_out(count=4)
    assert result.scenario_id == "FAN_OUT"
    assert result.expected_label == 1
    assert len(result.transactions) == 4
    receivers = {tx.receiver for tx in result.transactions}
    assert len(receivers) == 4  # 4 distinct recipients


def test_repeated_transfers_generation(generator: TransactionGenerator) -> None:
    """Test generating repeated transfers between identical accounts."""
    result = generator.generate_repeated_transfers(count=3)
    assert result.scenario_id == "REPEATED_TRANSFER"
    assert result.expected_label == 1
    assert len(result.transactions) == 3
    pair = {(tx.sender, tx.receiver) for tx in result.transactions}
    assert len(pair) == 1  # Identical pair


def test_contract_call_generation(generator: TransactionGenerator) -> None:
    """Test generating transactions with smart contract calldata."""
    result = generator.generate_contract_call(count=2)
    assert result.scenario_id == "CONTRACT_CALL"
    assert result.expected_label == 1
    assert len(result.transactions) == 2
    for tx in result.transactions:
        assert tx.input_data != "0x"
        assert tx.input_data.startswith("0xa9059cbb")


def test_run_all_scenarios_and_manifest(
    generator: TransactionGenerator, tmp_path: Path
) -> None:
    """Test batch scenario execution, mining transition, and manifest recording."""
    manifest_file = tmp_path / "test_manifest.json"
    results = generator.run_all_scenarios(
        output_manifest_path=manifest_file,
        automine=False,
        mine_after=True,
    )
    assert len(results) == 8
    total_txs = sum(len(r.transactions) for r in results)
    assert total_txs >= 25

    # Verify manifest file
    assert manifest_file.exists()
    with open(manifest_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["chain_id"] == 31337
    assert data["total_scenarios"] == 8
    assert data["total_transactions"] == total_txs
    assert len(data["scenarios"]) == 8

    # Verify all transactions reached terminal CONFIRMED state
    for r in results:
        assert r.confirmed_count == len(r.transactions)
        assert r.pending_count == 0
        for tx in r.transactions:
            assert tx.status == TransactionStatus.CONFIRMED
            assert tx.block_number is not None
