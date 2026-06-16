#!/usr/bin/env python3
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pyarrow as pa
import pyarrow.dataset as pds, pyarrow.compute as pc
import yaml, bisect
from collections import defaultdict
from value_engine import VALUE_MIN_GAME_TIME, VALUE_MIN_NW_LEAD, VALUE_MIN_EDGE, VALUE_MAX_PRICE, VALUE_TRADE_USD
import winprob
COST_HALF_SPREAD = 0.005

def load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f: mk = yaml.safe_load(f)
    m2info = {}
    for m in mk["markets"]:
        mid = m.get("dota_match_id")
        if mid and mid.isdigit() and mid != "123":
            m2info[mid] = {"yes": m["yes_token_id"], "no": m["no_token_id"], "side": m.get("steam_side_mapping", "normal"), "radiant_team_id": m.get("steam_radiant_team"), "dire_team_id": m.get("steam_dire_team")}
    return m2info

ds_snap = pds.dataset(REPO_ROOT / "data_v2/snapshots", format="parquet", partitioning="hive")
t_snap = ds_snap.to_table(columns=["match_id", "received_at_ns", "game_time_sec", "radiant_lead", "data_source"], filter=pc.field("data_source") == "top_live")
snaps = defaultdict(list)
for i in range(t_snap.num_rows):
    match_id = t_snap["match_id"][i].as_py()
    snaps[match_id].append({"ns": t_snap["received_at_ns"][i].as_py(), "gt": t_snap["game_time_sec"][i].as_py(), "rl": t_snap["radiant_lead"][i].as_py()})
for mid in snaps: snaps[mid].sort(key=lambda x: x["ns"])

m2info = load_mapping()
joinable = [m for m in snaps if m in m2info]
tokens = set()
for m in joinable:
    tokens.add(m2info[m]["yes"])
    tokens.add(m2info[m]["no"])

ds_book = pds.dataset(REPO_ROOT / "data_v2/book_ticks", format="parquet", partitioning="hive")
t_book = ds_book.to_table(columns=["asset_id", "received_at_ns", "mid"], filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))))
by_asset = defaultdict(list)
for i in range(t_book.num_rows):
    if t_book["mid"][i].as_py() is not None:
        by_asset[t_book["asset_id"][i].as_py()].append((t_book["received_at_ns"][i].as_py(), t_book["mid"][i].as_py()))
for a in by_asset: by_asset[a].sort()
book = {a: ([x[0] for x in by_asset[a]], [x[1] for x in by_asset[a]]) for a in by_asset}

joined_mids = [mid for mid in joinable if m2info[mid]["yes"] in book and m2info[mid]["no"] in book]

counts = {"total_matches": len(joined_mids), "unresolved": 0, "no_valid_snapshots": 0}
snap_drops = {"too_early": 0, "lead_too_small": 0, "no_book": 0, "price_too_high": 0, "edge_too_small": 0, "already_entered": 0}

trades = 0

samples_price_high = []
samples_edge_small = []

import random

for mid in joined_mids:
    info = m2info[mid]
    y_tok = info["yes"]; n_tok = info["no"]
    arr = book.get(y_tok)
    if not arr or not arr[1] or 0.10 <= arr[1][-1] <= 0.90:
        counts["unresolved"] += 1
        continue
        
    entered = {"YES": False, "NO": False}
    valid_snaps = 0
    for cur in snaps[mid]:
        gt = cur["gt"]; rl = cur["rl"]
        if gt is None or gt < VALUE_MIN_GAME_TIME: snap_drops["too_early"] += 1; continue
        if rl is None: continue
        try: rl = int(rl)
        except: continue
        if abs(rl) < VALUE_MIN_NW_LEAD: snap_drops["lead_too_small"] += 1; continue
        
        dir = "radiant" if rl > 0 else "dire"
        sm = info["side"]
        if sm == "normal": side = "YES" if dir == "radiant" else "NO"
        else: side = "NO" if dir == "radiant" else "YES"
        
        if entered[side]: snap_drops["already_entered"] += 1; continue
        
        tt = y_tok if side == "YES" else n_tok
        arr2 = book.get(tt)
        if not arr2: continue
        i = bisect.bisect_right(arr2[0], cur["ns"]) - 1
        if i < 0: snap_drops["no_book"] += 1; continue
        mid0 = arr2[1][i]
        
        ask = mid0 + 0.005
        
        elo_diff = winprob.elo_diff(info["radiant_team_id"], info["dire_team_id"]) if dir == "radiant" else winprob.elo_diff(info["dire_team_id"], info["radiant_team_id"])
        if elo_diff is None: elo_diff = 0
        fair = winprob.fair(abs(rl), gt, elo_diff)
        edge = fair - ask
        
        if ask > VALUE_MAX_PRICE:
            snap_drops["price_too_high"] += 1
            if len(samples_price_high) < 100: samples_price_high.append({"mid": mid, "gt": gt, "rl": rl, "ask": ask, "fair": fair, "edge": edge})
            continue

        if edge < VALUE_MIN_EDGE:
            snap_drops["edge_too_small"] += 1
            if len(samples_edge_small) < 100: samples_edge_small.append({"mid": mid, "gt": gt, "rl": rl, "ask": ask, "fair": fair, "edge": edge})
            continue
        
        trades += 1
        valid_snaps += 1
        entered[side] = True
        
    if valid_snaps == 0:
        counts["no_valid_snapshots"] += 1

print("Matches:", counts)
print("Snapshot Drops:", snap_drops)
print("Trades:", trades)

random.seed(42)
print("\\n--- PRICE TOO HIGH SAMPLES ---")
for s in random.sample(samples_price_high, min(5, len(samples_price_high))):
    print(f"Match {s['mid']}: gt={s['gt']}s, lead={s['rl']}, ask={s['ask']:.3f}, fair={s['fair']:.3f}, edge={s['edge']:.3f}")

print("\\n--- EDGE TOO SMALL SAMPLES ---")
for s in random.sample(samples_edge_small, min(5, len(samples_edge_small))):
    print(f"Match {s['mid']}: gt={s['gt']}s, lead={s['rl']}, ask={s['ask']:.3f}, fair={s['fair']:.3f}, edge={s['edge']:.3f}")
