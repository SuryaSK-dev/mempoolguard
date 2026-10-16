import sys, httpx
from datetime import datetime, timezone
from web3 import Web3

h, model = sys.argv[1], sys.argv[2]
t = Web3(Web3.HTTPProvider("http://127.0.0.1:8545")).eth.get_transaction(h)
body = {"transaction": {
    "tx_hash": h, "chain_id": 31337,
    "sender": t["from"].lower(), "receiver": t["to"].lower(),
    "value_wei": int(t["value"]), "nonce": int(t["nonce"]),
    "gas_limit": int(t["gas"]), "gas_price_wei": int(t["gasPrice"]),
    "input_data": "0x" + bytes(t["input"]).hex(),
    "first_seen_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}, "model_name": model}
for path in ("predict", "decision"):
    r = httpx.post(f"http://127.0.0.1:8000/{path}", json=body, timeout=15)
    print(path, r.status_code, r.json(), "\n")
print("audit", httpx.get(f"http://127.0.0.1:8000/audit/{h}").json())
