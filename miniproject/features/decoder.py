"""Decoder for raw mempool transaction payloads into validated Transaction models.

Preserves the boundary between raw network ingestion and structured pipeline
contracts, strictly maintaining pre-confirmation semantics (block_number=None).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.contracts import Transaction, TransactionStatus, utc_now


def decode_payload_dict(
    payload: dict[str, Any],
    first_seen_at: datetime | None = None,
) -> Transaction:
    """Decode an extracted EVM transaction dictionary into a validated Transaction contract.

    Args:
        payload: Dictionary of transaction fields as returned by Web3 or JSON deserialization.
        first_seen_at: UTC timestamp when transaction was first observed. Defaults to now.

    Returns:
        Validated pre-confirmation Transaction instance with blockNumber=None.
    """
    tx_hash = str(payload.get("hash") or payload.get("tx_hash", ""))
    chain_id = int(payload.get("chainId") or 31337)
    sender = str(payload.get("from") or payload.get("sender", ""))
    receiver_raw = payload.get("to") or payload.get("receiver")
    receiver = str(receiver_raw) if receiver_raw else None

    # Handle value parsing
    value_raw = payload.get("value", 0)
    value_wei = int(value_raw) if value_raw is not None else 0

    nonce = int(payload.get("nonce", 0))
    gas_limit = int(payload.get("gas") or payload.get("gas_limit", 21000))

    # Gas price and EIP-1559 fee fields
    gas_price_raw = payload.get("gasPrice") or payload.get("gas_price_wei")
    gas_price_wei = int(gas_price_raw) if gas_price_raw is not None else None

    max_fee_raw = payload.get("maxFeePerGas") or payload.get("max_fee_per_gas_wei")
    max_fee_per_gas_wei = int(max_fee_raw) if max_fee_raw is not None else None

    max_priority_fee_raw = payload.get("maxPriorityFeePerGas") or payload.get("max_priority_fee_per_gas_wei")
    max_priority_fee_per_gas_wei = (
        int(max_priority_fee_raw) if max_priority_fee_raw is not None else None
    )

    input_data = str(payload.get("input") or payload.get("input_data") or "0x")
    if not input_data.startswith("0x"):
        input_data = "0x" + input_data

    # Ensure UTC timestamp
    if first_seen_at is None:
        first_seen_at = utc_now()
    elif first_seen_at.tzinfo is None:
        first_seen_at = first_seen_at.replace(tzinfo=timezone.utc)
    else:
        first_seen_at = first_seen_at.astimezone(timezone.utc)

    # Strictly enforce pre-confirmation status for mempool evaluation
    return Transaction(
        tx_hash=tx_hash,
        chain_id=chain_id,
        sender=sender,
        receiver=receiver,
        value_wei=value_wei,
        nonce=nonce,
        gas_limit=gas_limit,
        gas_price_wei=gas_price_wei,
        max_fee_per_gas_wei=max_fee_per_gas_wei,
        max_priority_fee_per_gas_wei=max_priority_fee_per_gas_wei,
        input_data=input_data,
        first_seen_at=first_seen_at,
        confirmed_at=None,
        block_number=None,
        transaction_status=TransactionStatus.PENDING,
        feature_availability={
            "transaction": True,
            "gas": True,
            "address_history": True,
            "temporal_window": True,
            "graph": True,
            "post_confirmation": False,
        },
    )


def decode_raw_transaction(raw_record: dict[str, Any]) -> Transaction:
    """Decode a database raw_transactions record into a validated Transaction contract.

    Args:
        raw_record: Dictionary containing 'raw_payload' (JSON string) and 'first_seen_at'.

    Returns:
        Validated pre-confirmation Transaction instance.
    """
    raw_payload_str = raw_record["raw_payload"]
    payload = json.loads(raw_payload_str)

    first_seen = raw_record["first_seen_at"]
    if isinstance(first_seen, str):
        first_seen_dt = datetime.fromisoformat(first_seen)
    else:
        first_seen_dt = first_seen

    # Ensure hash from record is respected if missing in payload
    if "hash" not in payload and "tx_hash" in raw_record:
        payload["hash"] = raw_record["tx_hash"]

    return decode_payload_dict(payload=payload, first_seen_at=first_seen_dt)
