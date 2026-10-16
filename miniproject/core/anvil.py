"""Anvil EVM Controller for local blockchain lifecycle management."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from web3 import Web3


class AnvilController:
    """Manages local Anvil EVM node lifecycle, mining policies, and RPC connections."""

    DEFAULT_PORT = 8545
    DEFAULT_CHAIN_ID = 31337

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        chain_id: int = DEFAULT_CHAIN_ID,
    ) -> None:
        self.host = host
        self.port = port
        self.chain_id = chain_id
        self.rpc_http_url = f"http://{host}:{port}"
        self.rpc_ws_url = f"ws://{host}:{port}"
        self._process: subprocess.Popen | None = None
        self._w3: Web3 | None = None

    @classmethod
    def find_anvil_binary(cls) -> str | None:
        """Locate anvil executable in system PATH or standard Foundry directories."""
        # 1. Check PATH
        path_bin = shutil.which("anvil")
        if path_bin:
            return path_bin

        # 2. Check standard Windows Foundry install path
        user_home = Path.home()
        candidates = [
            user_home / ".foundry" / "bin" / "anvil.exe",
            Path("C:/Program Files/Foundry/bin/anvil.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

        return None

    def get_web3(self) -> Web3:
        """Return an active Web3 instance connected to the local node."""
        if self._w3 is None or not self._w3.is_connected():
            self._w3 = Web3(Web3.HTTPProvider(self.rpc_http_url))
        return self._w3

    def is_running(self, timeout_seconds: float = 1.0) -> bool:
        """Check if an Anvil node is responding on the configured RPC endpoint."""
        req_body = b'{"jsonrpc":"2.0","method":"web3_clientVersion","params":[],"id":1}'
        req = urllib.request.Request(
            self.rpc_http_url,
            data=req_body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    version = data.get("result", "")
                    return "anvil" in version.lower()
                return False
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
            return False

    def start(self, block_time: int | None = None) -> None:
        """Start a local Anvil process if one is not already running."""
        if self.is_running():
            return

        bin_path = self.find_anvil_binary()
        if not bin_path:
            raise FileNotFoundError(
                "Anvil executable not found. Ensure Foundry is installed in PATH or ~/.foundry/bin"
            )

        cmd = [
            bin_path,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--chain-id",
            str(self.chain_id),
        ]
        if block_time and block_time > 0:
            cmd.extend(["--block-time", str(block_time)])

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        # Wait up to 5 seconds for node to become ready
        start_time = time.time()
        while time.time() - start_time < 5.0:
            if self.is_running():
                return
            time.sleep(0.1)

        raise TimeoutError(f"Anvil failed to start within 5 seconds on {self.rpc_http_url}")

    def stop(self) -> None:
        """Terminate the managed Anvil subprocess if started by this instance."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def get_client_version(self) -> str:
        """Return the Anvil client version string."""
        w3 = self.get_web3()
        return w3.client_version

    def get_accounts(self) -> list[str]:
        """Return available local development accounts."""
        w3 = self.get_web3()
        return list(w3.eth.accounts)

    def set_automine(self, enabled: bool) -> bool:
        """Enable or disable auto-mining on the local node.

        When disabled, submitted transactions remain in the mempool (pending)
        until explicitly mined via mine_blocks().
        """
        w3 = self.get_web3()
        res = w3.provider.make_request("evm_setAutomine", [enabled])
        if "error" in res:
            res = w3.provider.make_request("anvil_setAutomine", [enabled])
        return "error" not in res

    def set_interval_mining(self, seconds: int) -> bool:
        """Set continuous block interval mining in seconds."""
        w3 = self.get_web3()
        res = w3.provider.make_request("anvil_setIntervalMining", [seconds])
        return "error" not in res

    def mine_blocks(self, count: int = 1) -> int:
        """Explicitly mine N blocks to confirm pending mempool transactions."""
        w3 = self.get_web3()
        if count == 1:
            res = w3.provider.make_request("evm_mine", [])
        else:
            res = w3.provider.make_request("anvil_mine", [hex(count)])
        if "error" in res:
            raise RuntimeError(f"Mining error: {res['error']}")
        return w3.eth.block_number

    def get_pending_transactions(self) -> list[str]:
        """Retrieve hashes of transactions currently pending in the mempool."""
        w3 = self.get_web3()
        pending_block = w3.eth.get_block("pending", full_transactions=False)
        txs = pending_block.get("transactions", [])
        return [
            ("0x" + tx.hex().lower().removeprefix("0x"))
            if hasattr(tx, "hex")
            else str(tx)
            for tx in txs
        ]

    def mine_all_pending(self, max_blocks: int = 10) -> int:
        """Mine blocks repeatedly until the pending transaction pool is empty."""
        w3 = self.get_web3()
        blocks_mined = 0
        while blocks_mined < max_blocks:
            pending = w3.eth.get_block("pending", full_transactions=False).get("transactions", [])
            if not pending:
                break
            w3.provider.make_request("evm_mine", [])
            blocks_mined += 1
        return w3.eth.block_number

    def reset(self) -> bool:
        """Reset the local Anvil blockchain back to genesis state with zero nonces."""
        w3 = self.get_web3()
        res = w3.provider.make_request("anvil_reset", [])
        return "error" not in res
