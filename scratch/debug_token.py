import asyncio
import os
import json
import yaml
from live_executor import LiveCLOBClient
from dotenv import load_dotenv
from py_clob_client_v2 import BalanceAllowanceParams, AssetType

load_dotenv()

async def debug_token():
    client = LiveCLOBClient()
    tid = "39324475784383976532815240616554700800435245122420305146020726994307102547992"
    params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=tid)
    resp = await asyncio.to_thread(client._client.get_balance_allowance, params)
    print(json.dumps(resp, indent=2))

if __name__ == "__main__":
    asyncio.run(debug_token())
