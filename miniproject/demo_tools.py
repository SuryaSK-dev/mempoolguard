import sys
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
cmd = sys.argv[1]
if cmd == "off":
    print(w3.provider.make_request("evm_setAutomine", [False]))
elif cmd == "on":
    print(w3.provider.make_request("evm_setAutomine", [True]))
elif cmd == "mine":
    w3.provider.make_request("evm_mine", [])
    print("block", w3.eth.block_number)
elif cmd == "send":
    a = w3.eth.accounts
    h = w3.eth.send_transaction({"from": a[0], "to": a[1], "value": w3.to_wei(0.05, "ether")})
    print(h.to_0x_hex())
