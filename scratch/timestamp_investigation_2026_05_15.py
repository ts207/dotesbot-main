#!/usr/bin/env python3
"""
Timestamp investigation report for Dota 2 Polymarket signal analysis.
For each target timestamp, shows game state snapshots, book data, events, and signals.
"""

import sys
import os
import csv
import yaml
from datetime import datetime, timezone
from collections import defaultdict

# Add parent dir to path for structure_state import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from structure_state import decode_structure_state

# ── Paths ────────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(BASE, "logs")

SNAPSHOTS_CSV  = os.path.join(LOGS, "raw_snapshots.csv")
BOOK_CSV       = os.path.join(LOGS, "book_events.csv")
EVENTS_CSV     = os.path.join(LOGS, "dota_events.csv")
SIGNALS_CSV    = os.path.join(LOGS, "signals.csv")
MARKETS_YAML   = os.path.join(BASE, "markets.yaml")
OUTPUT_FILE    = os.path.join(LOGS, "timestamp_report.txt")

# ── Match/token mappings ──────────────────────────────────────────────────────
MATCH_IDS = {
    "8811839782": "REKONIX vs Tundra Esports G1",
    "8812155277": "Aurora vs GamerLegion G1",
    "8812138607": "Vici Gaming vs ex-HEROIC G1",
    "8812138635": "Team Liquid vs Team Falcons G1",
    "8812138585": "Virtus.pro vs Team Spirit G1",
    "8812233361": "Virtus.pro vs Team Spirit G2",
    "8811845697": "PlayTime vs BetBoom Team G1",
    "8812263063": "Aurora vs GamerLegion G2",
    "8812274718": "Vici Gaming vs ex-HEROIC G2",
    "8812258249": "Team Liquid vs Team Falcons G2",
    # Corrected entries (bot not running during these games)
    "8811618995": "Vici Gaming vs Team Falcons G1",
    "8811618989": "GamerLegion vs Team Spirit G1",
    "8811706161": "GamerLegion vs Team Spirit G2",
    "8811620119": "Aurora vs ex-HEROIC G1",
    "8811680490": "Aurora vs ex-HEROIC G2",
    "8811618959": "Virtus.pro vs Team Liquid G1",
    "8811683230": "Virtus.pro vs Team Liquid G2",
}

YES_TEAM = {
    "8811839782": "REKONIX",
    "8812155277": "Aurora",
    "8812138607": "Vici Gaming",
    "8812138635": "Team Liquid",
    "8812138585": "Virtus.pro",
    "8812233361": "Virtus.pro",
    "8811845697": "PlayTime",
    "8812263063": "Aurora",
    "8812274718": "Vici Gaming",
    "8812258249": "Team Liquid",
    # Corrected entries
    "8811618995": "Vici Gaming",
    "8811618989": "GamerLegion",
    "8811706161": "GamerLegion",
    "8811620119": "Aurora",
    "8811680490": "Aurora",
    "8811618959": "Virtus.pro",
    "8811683230": "Virtus.pro",
}

# Targets
targets = [
    ("8811839782", 3224, "REKONIX vs Tundra G1 @ 53:44"),
    ("8812155277", 2467, "Aurora vs GamerLegion G1 @ 41:07"),
    ("8812138607", 4732, "Vici vs ex-HEROIC G1 @ 78:52"),
    ("8812138635", 1160, "Liquid vs Falcons G1 @ 19:20"),
    ("8811845697", 2708, "BetBoom vs PlayTime G1 @ 45:08"),
    ("8811839782", 2271, "REKONIX vs Tundra G1 @ 37:51"),
    ("8811839782", 3285, "REKONIX vs Tundra G1 @ 54:45"),
    # CORRECTED: was Liquid vs Falcons G1 — user said "falcons vs vici map1"
    ("8811618995", 2477, "Vici vs Falcons G1 @ 41:17"),
    # CORRECTED: was VP vs Spirit G1 — user said "gamerlegion vs spirit"
    ("8811618989", 2686, "GamerLegion vs Spirit G1 @ 44:46"),
    # CORRECTED: was VP vs Spirit G2 — user said "gamerlegion vs spirit g2"
    ("8811706161", 3763, "GamerLegion vs Spirit G2 @ 62:43"),
    # CORRECTED: was Vici vs ex-HEROIC G2 — user said "heroic vs aurora map1"
    ("8811620119", 2123, "ex-HEROIC vs Aurora G1 @ 35:23"),
    # CORRECTED: was Aurora vs GamerLegion G2 — user said "heroic vs aurora map2"
    ("8811680490", 3069, "ex-HEROIC vs Aurora G2 @ 51:09"),
    # CORRECTED: was Liquid vs Falcons G2 — user said "liquid vs vp"
    ("8811618959", 2389, "VP vs Liquid G1 @ 39:49"),
    # CORRECTED: was Liquid vs Falcons G2 — user said "liquid vs vp map2"
    ("8811683230", 1879, "VP vs Liquid G2 @ 31:19"),
]


# ── Utility ───────────────────────────────────────────────────────────────────
def fmt_gt(secs):
    return f"{secs//60}:{secs%60:02d}"


def decode_tower_str(tower_state_int):
    snap = {"tower_state": tower_state_int, "match_id": ""}
    ss = decode_structure_state(snap)
    if ss.confidence < 1.0:
        return f"raw={tower_state_int}"
    return (
        f"R:T1={ss.radiant_t1_alive}T2={ss.radiant_t2_alive}"
        f"T3={ss.radiant_t3_alive}T4={ss.radiant_t4_alive} "
        f"D:T1={ss.dire_t1_alive}T2={ss.dire_t2_alive}"
        f"T3={ss.dire_t3_alive}T4={ss.dire_t4_alive}"
    )


def decode_building_str(building_state_int):
    """Bits 0-5 = radiant barracks (alive=set), 6-11 = dire barracks."""
    b = int(building_state_int)
    r_bits = b & 0x3F
    d_bits = (b >> 6) & 0x3F
    r_alive = bin(r_bits).count("1")
    d_alive = bin(d_bits).count("1")
    return f"R_rax={r_alive}/6 D_rax={d_alive}/6 (raw={building_state_int})"


def parse_ts(ts_str):
    """Parse ISO timestamp to float unix seconds."""
    ts_str = ts_str.strip()
    if not ts_str:
        return None
    # Handle +00:00 suffix
    ts_str = ts_str.replace("+00:00", "+00:00")
    try:
        # Python 3.7+ fromisoformat doesn't handle +00:00 well in all versions
        ts_str2 = ts_str
        if ts_str2.endswith("+00:00"):
            ts_str2 = ts_str2[:-6] + "Z"
        if ts_str2.endswith("Z"):
            dt = datetime.strptime(ts_str2[:-1], "%Y-%m-%dT%H:%M:%S.%f")
        else:
            dt = datetime.strptime(ts_str2, "%Y-%m-%dT%H:%M:%S.%f")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        try:
            dt = datetime.fromisoformat(ts_str)
            return dt.timestamp()
        except Exception:
            return None


# ── Load data ─────────────────────────────────────────────────────────────────
print("Loading data...", flush=True)

# Load markets.yaml to get token IDs
with open(MARKETS_YAML) as f:
    m = yaml.safe_load(f)
market_entries = m["markets"]

match_to_yes_token = {}
match_to_no_token = {}
for entry in market_entries:
    mid = str(entry.get("dota_match_id", ""))
    if mid in MATCH_IDS:
        match_to_yes_token[mid] = str(entry.get("yes_token_id", ""))
        match_to_no_token[mid] = str(entry.get("no_token_id", ""))

# Load raw_snapshots
snapshots_by_match = defaultdict(list)
with open(SNAPSHOTS_CSV, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        mid = str(row.get("match_id", "")).strip()
        if mid in MATCH_IDS:
            gt = row.get("game_time_sec", "")
            if gt != "":
                row["_gt"] = int(float(gt))
                ts_val = parse_ts(row.get("received_at_utc", ""))
                row["_ts"] = ts_val
                snapshots_by_match[mid].append(row)

for mid in snapshots_by_match:
    snapshots_by_match[mid].sort(key=lambda r: (r["_gt"], r.get("received_at_ns", 0)))

# Deduplicate by gt (keep first occurrence per gt)
for mid in snapshots_by_match:
    seen_gt = {}
    deduped = []
    for row in snapshots_by_match[mid]:
        gt = row["_gt"]
        if gt not in seen_gt:
            seen_gt[gt] = True
            deduped.append(row)
    snapshots_by_match[mid] = deduped

# Load book events
book_by_asset = defaultdict(list)
with open(BOOK_CSV, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        asset = str(row.get("asset_id", "")).strip()
        ts_val = parse_ts(row.get("timestamp_utc", ""))
        if ts_val is not None:
            row["_ts"] = ts_val
            book_by_asset[asset].append(row)

for asset in book_by_asset:
    book_by_asset[asset].sort(key=lambda r: r["_ts"])

# Load dota_events
events_by_match = defaultdict(list)
with open(EVENTS_CSV, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        mid = str(row.get("match_id", "")).strip()
        events_by_match[mid].append(row)

# Load signals
signals_by_match = defaultdict(list)
with open(SIGNALS_CSV, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        mid = str(row.get("match_id", "")).strip()
        signals_by_match[mid].append(row)

print("Data loaded.", flush=True)


# ── Helper: get 3 nearby snapshots (before, at/anchor, after) ────────────────
def get_nearby_snaps(match_id, target_gt):
    snaps = snapshots_by_match.get(match_id, [])
    if not snaps:
        return []
    gts = [s["_gt"] for s in snaps]
    # Find index of nearest
    best_i = min(range(len(gts)), key=lambda i: abs(gts[i] - target_gt))
    # We want: one before anchor, anchor itself, one after
    indices = set()
    indices.add(best_i)
    if best_i > 0:
        indices.add(best_i - 1)
    if best_i < len(snaps) - 1:
        indices.add(best_i + 1)
    # Sort
    result = sorted(indices)
    # Ensure we have up to 3
    return [snaps[i] for i in result]


# ── Helper: get book ticks in window ─────────────────────────────────────────
def get_book_ticks(match_id, anchor_ts, window_sec=20):
    yes_token = match_to_yes_token.get(match_id, "")
    no_token = match_to_no_token.get(match_id, "")
    yes_ticks = []
    no_ticks = []
    for tick in book_by_asset.get(yes_token, []):
        offset = tick["_ts"] - anchor_ts
        if abs(offset) <= window_sec:
            yes_ticks.append((offset, tick))
    for tick in book_by_asset.get(no_token, []):
        offset = tick["_ts"] - anchor_ts
        if abs(offset) <= window_sec:
            no_ticks.append((offset, tick))
    return yes_ticks, no_ticks


# ── Helper: get events in window ─────────────────────────────────────────────
def get_events(match_id, target_gt, window_sec=60):
    result = []
    for ev in events_by_match.get(match_id, []):
        try:
            gt = int(float(ev.get("game_time_sec", 0)))
        except Exception:
            continue
        if abs(gt - target_gt) <= window_sec:
            result.append((gt, ev))
    result.sort(key=lambda x: x[0])
    return result


# ── Helper: get signals in window ────────────────────────────────────────────
def get_signals(match_id, target_gt, window_sec=60):
    result = []
    for sig in signals_by_match.get(match_id, []):
        try:
            gt = int(float(sig.get("game_time_sec", 0)))
        except Exception:
            continue
        if abs(gt - target_gt) <= window_sec:
            result.append((gt, sig))
    result.sort(key=lambda x: x[0])
    return result


# ── Helper: get YES mid at anchor ±Ns ────────────────────────────────────────
def get_yes_mid_at(match_id, anchor_ts, offset_sec=0, tolerance=5):
    yes_token = match_to_yes_token.get(match_id, "")
    target_ts = anchor_ts + offset_sec
    best = None
    best_dist = float("inf")
    for tick in book_by_asset.get(yes_token, []):
        mid_val = tick.get("mid", "")
        if mid_val == "" or mid_val is None:
            continue
        try:
            mid_f = float(mid_val)
        except Exception:
            continue
        dist = abs(tick["_ts"] - target_ts)
        if dist < best_dist and dist <= tolerance:
            best_dist = dist
            best = mid_f
    return best


# ── Helper: find anchor timestamp from snapshots ──────────────────────────────
def get_anchor_ts(match_id, target_gt):
    """Return the received_at timestamp of the nearest snapshot to target_gt."""
    snaps = snapshots_by_match.get(match_id, [])
    if not snaps:
        return None
    best = min(snaps, key=lambda s: abs(s["_gt"] - target_gt))
    return best.get("_ts")


# ── Report generation ─────────────────────────────────────────────────────────
lines = []

def emit(*args, **kwargs):
    lines.append(" ".join(str(a) for a in args))

def section(title):
    emit()
    emit("=" * 80)
    emit(title)
    emit("=" * 80)

def sub(title):
    emit()
    emit(f"  ── {title}")
    emit("  " + "-" * (len(title) + 4))


summary_rows = []

for idx, (match_id, target_gt, label) in enumerate(targets, 1):
    match_name = MATCH_IDS.get(match_id, match_id)
    yes_team = YES_TEAM.get(match_id, "YES")

    section(f"[{idx:02d}] {label}  |  Match: {match_name}")
    emit(f"  match_id={match_id}  target_gt={target_gt}s ({fmt_gt(target_gt)})  YES={yes_team}")

    # ── A) Game State ────────────────────────────────────────────────────────
    sub("A) Game State — 3 nearby snapshots")
    snaps = get_nearby_snaps(match_id, target_gt)
    if not snaps:
        emit("  [NO SNAPSHOT DATA for this match_id]")
    else:
        prev_snap = None
        anchor_ts = None
        for snap in snaps:
            gt = snap["_gt"]
            is_anchor = abs(gt - target_gt) == min(abs(s["_gt"] - target_gt) for s in snaps)
            marker = " ◄ ANCHOR" if is_anchor else ""
            if is_anchor and anchor_ts is None:
                anchor_ts = snap.get("_ts")

            rl = snap.get("radiant_lead", "?")
            rs = snap.get("radiant_score", "?")
            ds = snap.get("dire_score", "?")
            tw = snap.get("tower_state", "")
            bw = snap.get("building_state", "")
            go = snap.get("game_over", "?")

            try:
                tw_str = decode_tower_str(int(tw)) if tw not in ("", None) else "N/A"
            except Exception:
                tw_str = f"raw={tw}"
            try:
                bw_str = decode_building_str(int(bw)) if bw not in ("", None) else "N/A"
            except Exception:
                bw_str = f"raw={bw}"

            emit(f"    gt={gt:5d}s ({fmt_gt(gt)}){marker}")
            emit(f"      radiant_lead={rl}  kills={rs}-{ds}  game_over={go}")
            emit(f"      tower_state : {tw_str}")
            emit(f"      building    : {bw_str}")

            # Deltas vs previous
            if prev_snap is not None:
                try:
                    prev_rl = int(prev_snap.get("radiant_lead", 0))
                    cur_rl  = int(snap.get("radiant_lead", 0))
                    nw_delta = cur_rl - prev_rl
                    prev_rs = int(prev_snap.get("radiant_score", 0))
                    cur_rs  = int(snap.get("radiant_score", 0))
                    prev_ds = int(prev_snap.get("dire_score", 0))
                    cur_ds  = int(snap.get("dire_score", 0))
                    kill_delta = (cur_rs + cur_ds) - (prev_rs + prev_ds)
                    # Tower falls
                    prev_tw = int(prev_snap.get("tower_state", 0) or 0)
                    cur_tw  = int(snap.get("tower_state", 0) or 0)
                    prev_ss = decode_structure_state({"tower_state": prev_tw, "match_id": match_id})
                    cur_ss  = decode_structure_state({"tower_state": cur_tw, "match_id": match_id})
                    tower_changes = []
                    if prev_ss.confidence == 1.0 and cur_ss.confidence == 1.0:
                        for tier, pv, cv, side in [
                            ("T1", prev_ss.radiant_t1_alive, cur_ss.radiant_t1_alive, "R"),
                            ("T2", prev_ss.radiant_t2_alive, cur_ss.radiant_t2_alive, "R"),
                            ("T3", prev_ss.radiant_t3_alive, cur_ss.radiant_t3_alive, "R"),
                            ("T4", prev_ss.radiant_t4_alive, cur_ss.radiant_t4_alive, "R"),
                            ("T1", prev_ss.dire_t1_alive, cur_ss.dire_t1_alive, "D"),
                            ("T2", prev_ss.dire_t2_alive, cur_ss.dire_t2_alive, "D"),
                            ("T3", prev_ss.dire_t3_alive, cur_ss.dire_t3_alive, "D"),
                            ("T4", prev_ss.dire_t4_alive, cur_ss.dire_t4_alive, "D"),
                        ]:
                            if pv is not None and cv is not None and cv < pv:
                                tower_changes.append(f"{side}{tier}:{pv}->{cv}(fell {pv-cv})")
                    tc_str = ", ".join(tower_changes) if tower_changes else "none"
                    emit(f"      Δ vs prev: NW_delta={nw_delta:+d}  kills_delta={kill_delta:+d}  tower_falls=[{tc_str}]")
                except Exception as e:
                    emit(f"      Δ vs prev: [error: {e}]")
            prev_snap = snap

    # ── B) Book ──────────────────────────────────────────────────────────────
    sub("B) Book — YES/NO ticks ±20s around anchor")
    # Determine anchor_ts from snapshots or fallback
    if 'anchor_ts' not in dir() or anchor_ts is None:
        anchor_ts = get_anchor_ts(match_id, target_gt)
    else:
        if anchor_ts is None:
            anchor_ts = get_anchor_ts(match_id, target_gt)

    if anchor_ts is None:
        emit("  [No anchor timestamp available — no snapshot found]")
    else:
        anchor_dt = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        emit(f"  Anchor timestamp: {anchor_dt} UTC  (book window ±20s)")

        yes_ticks, no_ticks = get_book_ticks(match_id, anchor_ts, window_sec=20)

        # YES ticks
        emit()
        emit(f"  YES ({yes_team}) token ticks:")
        if not yes_ticks:
            emit("    [no YES ticks in window]")
        else:
            emit(f"    {'offset':>8s}  {'mid':>7s}  {'ask':>7s}  {'bid':>7s}  {'spread':>8s}  {'ask_sz':>10s}")
            prev_mid = None
            for offset, tick in sorted(yes_ticks, key=lambda x: x[0]):
                mid  = tick.get("mid", "")
                ask  = tick.get("best_ask", "")
                bid  = tick.get("best_bid", "")
                sprd = tick.get("spread", "")
                asksz = tick.get("ask_size", "")
                try:
                    mid_f = float(mid) if mid != "" else None
                except Exception:
                    mid_f = None
                flag = ""
                if mid_f is not None and prev_mid is not None and abs(mid_f - prev_mid) > 0.02:
                    flag = "  ◄ BIG MOVE"
                prev_mid = mid_f
                emit(f"    {offset:>+8.2f}s  {mid:>7}  {ask:>7}  {bid:>7}  {sprd:>8}  {asksz:>10}{flag}")

        # NO ticks
        emit()
        emit(f"  NO ({match_name.split(' vs ')[1].split(' G')[0] if ' vs ' in match_name else 'NO'}) token ticks:")
        if not no_ticks:
            emit("    [no NO ticks in window]")
        else:
            emit(f"    {'offset':>8s}  {'mid':>7s}  {'ask':>7s}  {'bid':>7s}  {'spread':>8s}  {'ask_sz':>10s}")
            prev_mid = None
            for offset, tick in sorted(no_ticks, key=lambda x: x[0]):
                mid  = tick.get("mid", "")
                ask  = tick.get("best_ask", "")
                bid  = tick.get("best_bid", "")
                sprd = tick.get("spread", "")
                asksz = tick.get("ask_size", "")
                try:
                    mid_f = float(mid) if mid != "" else None
                except Exception:
                    mid_f = None
                flag = ""
                if mid_f is not None and prev_mid is not None and abs(mid_f - prev_mid) > 0.02:
                    flag = "  ◄ BIG MOVE"
                prev_mid = mid_f
                emit(f"    {offset:>+8.2f}s  {mid:>7}  {ask:>7}  {bid:>7}  {sprd:>8}  {asksz:>10}{flag}")

    # ── C) Detected events ───────────────────────────────────────────────────
    sub("C) Detected Events — ±60s of anchor gt")
    evs = get_events(match_id, target_gt, window_sec=60)
    if not evs:
        emit("  [no events in window]")
    else:
        for gt_ev, ev in evs:
            offset = gt_ev - target_gt
            etype  = ev.get("event_type", "?")
            tier   = ev.get("event_tier", "?")
            family = ev.get("event_family", "?")
            qual   = ev.get("event_quality", "?")
            direction = ev.get("direction", ev.get("event_direction", "?"))
            delta  = ev.get("networth_delta", ev.get("delta", "?"))
            emit(f"    gt={gt_ev:5d}s ({offset:+5d}s) | {etype:<35s} | tier={tier} family={family} quality={qual} dir={direction} NW_delta={delta}")

    # ── D) Signal decisions ───────────────────────────────────────────────────
    sub("D) Signal Decisions — ±60s of anchor gt")
    sigs = get_signals(match_id, target_gt, window_sec=60)
    if not sigs:
        emit("  [no signals in window]")
    else:
        for gt_sig, sig in sigs:
            offset = gt_sig - target_gt
            etype    = sig.get("event_type", "?")
            decision = sig.get("decision", "?")
            skip_rsn = sig.get("skip_reason", "")
            side     = sig.get("side", "?")
            fair_p   = sig.get("fair_price", "?")
            exec_p   = sig.get("executable_price", "?")
            edge     = sig.get("executable_edge", "?")
            score    = sig.get("trade_score", "?")
            severity = sig.get("severity", "?")
            emit(f"    gt={gt_sig:5d}s ({offset:+5d}s) | {etype:<35s} | decision={decision:<8s} skip={skip_rsn}")
            emit(f"      side={side} fair={fair_p} exec={exec_p} edge={edge} trade_score={score} severity={severity}")

    # ── E) Key metrics ────────────────────────────────────────────────────────
    sub("E) Key Metrics")
    if anchor_ts is None:
        emit("  [no anchor ts — cannot compute metrics]")
        summary_rows.append({
            "idx": idx, "label": label, "gt": target_gt, "match": match_name,
            "P0": "N/A", "P5": "N/A", "P10": "N/A", "P30": "N/A",
            "move30": "N/A", "max_spread": "N/A",
            "events": len(evs), "signal": "N/A", "what_changed": "no_data",
        })
    else:
        yes_token = match_to_yes_token.get(match_id, "")
        # Collect all YES ticks in wider window for spread
        all_yes_ticks_wide = []
        for tick in book_by_asset.get(yes_token, []):
            offset = tick["_ts"] - anchor_ts
            if -5 <= offset <= 35:
                all_yes_ticks_wide.append((offset, tick))

        p0  = get_yes_mid_at(match_id, anchor_ts, 0,  tolerance=5)
        p5  = get_yes_mid_at(match_id, anchor_ts, 5,  tolerance=8)
        p10 = get_yes_mid_at(match_id, anchor_ts, 10, tolerance=8)
        p30 = get_yes_mid_at(match_id, anchor_ts, 30, tolerance=15)

        def fmt_p(v):
            return f"{v:.4f}" if v is not None else "N/A"

        move30 = None
        if p0 is not None and p30 is not None:
            move30 = p30 - p0

        max_spread = None
        for _, tick in all_yes_ticks_wide:
            sprd = tick.get("spread", "")
            if sprd not in ("", None):
                try:
                    sv = float(sprd)
                    if max_spread is None or sv > max_spread:
                        max_spread = sv
                except Exception:
                    pass

        emit(f"  P0  (anchor ±2s)   : {fmt_p(p0)}")
        emit(f"  P+5s               : {fmt_p(p5)}")
        emit(f"  P+10s              : {fmt_p(p10)}")
        emit(f"  P+30s              : {fmt_p(p30)}")
        emit(f"  Move 30s           : {fmt_p(move30)}")
        emit(f"  Max YES spread (-5s..+35s): {fmt_p(max_spread)}")

        # Determine what changed
        what_changed_parts = []
        if evs:
            event_types = list({ev.get("event_type", "") for _, ev in evs})
            what_changed_parts.append("+".join(event_types[:3]))
        if sigs:
            decisions = list({sig.get("decision", "skip") for _, sig in sigs})
            what_changed_parts.append(f"sig:{'+'.join(decisions)}")
        if not what_changed_parts:
            what_changed_parts.append("no_event")
        what_changed = "; ".join(what_changed_parts)

        sig_decision = "none"
        if sigs:
            # Most recent signal decision in window
            sig_decision = sigs[-1][1].get("decision", "none")

        summary_rows.append({
            "idx": idx, "label": label, "gt": target_gt, "match": match_name,
            "P0": fmt_p(p0), "P5": fmt_p(p5), "P10": fmt_p(p10), "P30": fmt_p(p30),
            "move30": fmt_p(move30), "max_spread": fmt_p(max_spread),
            "events": len(evs), "signal": sig_decision, "what_changed": what_changed,
        })

    # Reset anchor_ts for next iteration
    anchor_ts = None

# ── Summary table ─────────────────────────────────────────────────────────────
emit()
emit()
section("SUMMARY TABLE")
emit()
hdr = "{:>3}  {:<40} {:>7}  {:>7}  {:>7}  {:>7}  {:>8}  {:>8}  {:>5}  {:<12}  {}".format(
    "#", "Label", "GT", "P0", "P+5s", "P+30s", "Move30s", "MaxSprd", "Evts", "Signal", "What Changed"
)
emit(hdr)
emit("-" * len(hdr))
for row in summary_rows:
    emit(
        f"{row['idx']:>3}  {row['label']:<40} {row['gt']:>7}  "
        f"{row['P0']:>7}  {row['P5']:>7}  {row['P30']:>7}  "
        f"{row['move30']:>8}  {row['max_spread']:>8}  "
        f"{row['events']:>5}  {row['signal']:<12}  {row['what_changed']}"
    )

emit()
emit(f"Report generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
emit(f"Targets investigated: {len(targets)}")


# ── Write and print ───────────────────────────────────────────────────────────
report_text = "\n".join(lines)
with open(OUTPUT_FILE, "w") as f:
    f.write(report_text)
    f.write("\n")

print(report_text)
print(f"\n[Report saved to {OUTPUT_FILE}]")
