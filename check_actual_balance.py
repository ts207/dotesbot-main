import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

def main():
    rpc = "https://polygon-rpc.com"
    w3 = Web3(Web3.HTTPProvider(rpc))
    
    # USDC.e on Polygon (Polymarket uses this)
    usdc_addr = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    abi = [
        {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
        {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}
    ]
    
    usdc = w3.eth.contract(address=usdc_addr, abi=abi)
    dec = usdc.functions.decimals().call()
    
    eoa_key = os.getenv("POLY_PRIVATE_KEY")
    if eoa_key:
        addr = w3.eth.account.from_key(eoa_key).address
        bal = usdc.functions.balanceOf(addr).call()
        print(f"EOA {addr} USDC.e Balance: {bal / 10**dec:.2f}")
        
    funder = os.getenv("POLY_FUNDER_ADDRESS")
    if funder:
        bal = usdc.functions.balanceOf(funder).call()
        print(f"Funder {funder} USDC.e Balance: {bal / 10**dec:.2f}")

if __name__ == "__main__":
    main()
