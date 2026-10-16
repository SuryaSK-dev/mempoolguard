"""Pending transaction listener for local EVM mempool monitoring.

Listens to pending transactions on a local Anvil EVM node using JSON-RPC
filters with txpool / pending block fallback, deduplicates hashes, records
precise first-seen timestamps, and persists raw transaction payloads.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import TransactionNotFound, Web3Exception

from core.contracts import utc_now
from core.database import Database

logger = logging.getLogger("mempoolguard.listener")


def serialize_raw_transaction(tx_dict: dict[str, Any]) -> str:
    """Convert a Web3 transaction AttributeDict / mapping to a JSON string.

    Handles HexBytes, bytes, and integers safely.
    """

    def _encoder(val: Any) -> Any:
        if isinstance(val, HexBytes):
            return val.to_0x_hex()
        if isinstance(val, (bytes, bytearray)):
            return "0x" + val.hex()
        if isinstance(val, datetime):
            return val.isoformat()
        return val

    serializable = {k: _encoder(v) for k, v in tx_dict.items()}
    return json.dumps(serializable, sort_keys=True)


class MempoolListener:
    """Monitors local EVM mempool for pending transactions and persists raw observations."""

    def __init__(
        self,
        w3: Web3 | str,
        db: Database,
        poll_interval_seconds: float = 0.05,
        max_seen_cache: int = 50_000,
        on_transaction_captured: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize the mempool listener.

        Args:
            w3: Connected Web3 instance or HTTP RPC endpoint string.
            db: Target Database instance for raw transaction persistence.
            poll_interval_seconds: Frequency between polling cycles in the background thread.
            max_seen_cache: Maximum number of transaction hashes retained in memory for deduplication.
            on_transaction_captured: Optional hook invoked when a new pending transaction is stored.
        """
        if isinstance(w3, str):
            self.rpc_url = w3
            self.w3 = Web3(Web3.HTTPProvider(w3))
        else:
            self.w3 = w3
            self.rpc_url = getattr(w3.provider, "endpoint_uri", "custom_w3")

        self.db = db
        self.poll_interval_seconds = poll_interval_seconds
        self.max_seen_cache = max_seen_cache
        self.on_transaction_captured = on_transaction_captured

        # State and concurrency
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

        # In-memory deduplication cache
        self._seen_hashes: set[str] = set()
        self._seen_queue: deque[str] = deque()

        # RPC Filter state
        self._pending_filter: Any = None

        # Diagnostic metrics
        self.captured_count: int = 0
        self.duplicate_count: int = 0
        self.dropped_count: int = 0
        self.error_count: int = 0
        self.last_poll_time: datetime | None = None
        self.start_time: datetime | None = None

    def is_running(self) -> bool:
        """Return whether the background polling worker is actively running."""
        with self._lock:
            return self._running and (self._thread.is_alive() if self._thread else False)

    def _get_or_create_filter(self) -> Any:
        """Obtain active pending filter or create a new one on Anvil."""
        if self._pending_filter is None:
            try:
                self._pending_filter = self.w3.eth.filter("pending")
                logger.debug("Created pending transaction filter: %s", self._pending_filter.filter_id)
            except (Web3Exception, OSError, ValueError, KeyError) as exc:
                logger.warning("Failed creating pending filter on RPC: %s", exc)
                self._pending_filter = None
        return self._pending_filter

    def _mark_seen(self, tx_hash: str) -> bool:
        """Record transaction hash in bounded deduplication cache.

        Returns True if hash is fresh, False if already seen.
        """
        norm_hash = tx_hash.lower()
        with self._lock:
            if norm_hash in self._seen_hashes:
                self.duplicate_count += 1
                return False

            self._seen_hashes.add(norm_hash)
            self._seen_queue.append(norm_hash)

            # Evict oldest if exceeding capacity
            if len(self._seen_queue) > self.max_seen_cache:
                oldest = self._seen_queue.popleft()
                self._seen_hashes.discard(oldest)

            return True

    def poll_once(self) -> list[str]:
        """Execute a single polling sweep to capture pending transactions from the mempool.

        Returns a list of newly captured and persisted transaction hashes.
        """
        new_hashes: list[str] = []
        candidate_hashes: list[str] = []

        # 1. Primary Strategy: eth_getFilterChanges on pending filter
        flt = self._get_or_create_filter()
        if flt is not None:
            try:
                entries = flt.get_new_entries()
                for entry in entries:
                    h = entry.to_0x_hex() if hasattr(entry, "to_0x_hex") else str(entry)
                    candidate_hashes.append(h)
            except (Web3Exception, OSError, ValueError, KeyError) as exc:
                logger.warning("Filter query failed (%s); resetting filter", exc)
                with suppress(Web3Exception, OSError, ValueError):
                    if self._pending_filter:
                        self.w3.eth.uninstall_filter(self._pending_filter.filter_id)
                self._pending_filter = None

        # 2. Fallback / Complementary Strategy: Inspect pending block transactions
        # If filter yielded no transactions, check pending block as verified fallback
        if not candidate_hashes:
            try:
                pending_block = self.w3.eth.get_block("pending", full_transactions=False)
                if pending_block and "transactions" in pending_block:
                    for h_bytes in pending_block["transactions"]:
                        h = h_bytes.to_0x_hex() if hasattr(h_bytes, "to_0x_hex") else str(h_bytes)
                        if h.lower() not in self._seen_hashes:
                            candidate_hashes.append(h)
            except (Web3Exception, OSError, ValueError, KeyError) as exc:
                logger.debug("Pending block fallback query failed: %s", exc)

        # 3. Process each candidate hash
        for raw_hash in candidate_hashes:
            norm_hash = raw_hash.strip().lower()
            if not norm_hash.startswith("0x") or len(norm_hash) != 66:
                continue

            if not self._mark_seen(norm_hash):
                continue  # Duplicate

            # Fetch transaction details from mempool
            try:
                tx_data = self.w3.eth.get_transaction(norm_hash)
            except TransactionNotFound:
                logger.debug("Transaction %s no longer available in mempool", norm_hash)
                self.dropped_count += 1
                continue
            except (Web3Exception, OSError, ValueError) as exc:
                logger.warning("Error fetching transaction %s: %s", norm_hash, exc)
                self.error_count += 1
                continue

            if tx_data is None:
                self.dropped_count += 1
                continue

            # Record exact observation timestamp and serialize raw payload
            first_seen_at = utc_now()
            tx_dict = dict(tx_data)
            raw_json = serialize_raw_transaction(tx_dict)

            # Persist to database
            try:
                inserted = self.db.insert_raw_transaction(
                    tx_hash=norm_hash,
                    raw_payload=raw_json,
                    first_seen_at=first_seen_at,
                )
                if inserted:
                    with self._lock:
                        self.captured_count += 1
                    new_hashes.append(norm_hash)

                    if self.on_transaction_captured:
                        try:
                            self.on_transaction_captured(
                                {
                                    "tx_hash": norm_hash,
                                    "first_seen_at": first_seen_at,
                                    "block_number": tx_data.get("blockNumber"),
                                    "raw_payload": raw_json,
                                }
                            )
                        except (RuntimeError, ValueError, TypeError, KeyError) as cb_exc:
                            logger.error("Callback execution failed for %s: %s", norm_hash, cb_exc)
            except (sqlite3.Error, OSError) as db_exc:
                logger.error("Database insert failed for %s: %s", norm_hash, db_exc)
                self.error_count += 1

        self.last_poll_time = utc_now()
        return new_hashes

    def _worker_loop(self) -> None:
        """Background thread worker loop continuously polling the mempool."""
        logger.info("MempoolListener background thread started.")
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except (Web3Exception, OSError, ConnectionError, TimeoutError) as exc:
                logger.warning("RPC error in polling loop: %s", exc)
                with self._lock:
                    self.error_count += 1
                # Back off slightly on network/RPC error
                time.sleep(0.2)
            except Exception:
                logger.exception("Unexpected error in MempoolListener")
                with self._lock:
                    self.error_count += 1

            self._stop_event.wait(self.poll_interval_seconds)

        logger.info("MempoolListener background thread exiting.")

    def start(self) -> None:
        """Start the background mempool listener thread."""
        with self._lock:
            if self._running:
                return

            if not self.w3.is_connected():
                raise ConnectionError(f"Cannot connect to EVM RPC node at {self.rpc_url}")

            self._stop_event.clear()
            self._running = True
            self.start_time = utc_now()

            self._thread = threading.Thread(
                target=self._worker_loop,
                name="MempoolListenerThread",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Stop the background mempool listener thread and release resources."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

        # Clean up RPC filter
        if self._pending_filter is not None:
            with suppress(Web3Exception, OSError, ValueError):
                self.w3.eth.uninstall_filter(self._pending_filter.filter_id)
            self._pending_filter = None

    def get_metrics(self) -> dict[str, Any]:
        """Return diagnostic metrics and operational health statistics."""
        with self._lock:
            uptime_seconds = (
                (datetime.now(timezone.utc) - self.start_time).total_seconds()
                if self.start_time and self._running
                else 0.0
            )
            is_active = self._running and (self._thread.is_alive() if self._thread else False)
            return {
                "running": is_active,
                "captured_count": self.captured_count,
                "duplicate_count": self.duplicate_count,
                "dropped_count": self.dropped_count,
                "error_count": self.error_count,
                "in_memory_seen_count": len(self._seen_hashes),
                "last_poll_time": self.last_poll_time.isoformat() if self.last_poll_time else None,
                "uptime_seconds": round(uptime_seconds, 2),
            }


def main() -> None:
    """CLI runner to start live mempool listener and print observed transactions."""
    parser = argparse.ArgumentParser(description="MempoolGuard Mempool Listener")
    parser.add_argument("--rpc", default="http://127.0.0.1:8545", help="Anvil RPC URL")
    parser.add_argument("--db", default="data/mempoolguard.db", help="SQLite database path")
    parser.add_argument(
        "--interval", type=float, default=0.05, help="Poll interval in seconds (default: 0.05s)"
    )
    parser.add_argument(
        "--duration", type=float, default=10.0, help="Run duration in seconds before stopping"
    )
    args = parser.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to Anvil at {args.rpc}")
        return

    db = Database(args.db)
    db.init_schema()

    def on_captured(tx_info: dict[str, Any]) -> None:
        print(
            f" [CAPTURED PENDING TX] Hash: {tx_info['tx_hash'][:16]}... "
            f"| First Seen: {tx_info['first_seen_at'].isoformat()} "
            f"| Block: {tx_info['block_number']}"
        )

    listener = MempoolListener(
        w3=w3,
        db=db,
        poll_interval_seconds=args.interval,
        on_transaction_captured=on_captured,
    )

    print(f"Starting MempoolListener on {args.rpc} (Duration: {args.duration}s)...")
    listener.start()
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        print("\nStopping listener...")
    finally:
        listener.stop()
        metrics = listener.get_metrics()
        print("\nListener Stopped. Metrics:")
        print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
