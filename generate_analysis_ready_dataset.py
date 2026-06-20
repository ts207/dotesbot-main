import os
import json
import pandas as pd
from pathlib import Path
from collections import defaultdict

class OutcomeAggregator:
    def __init__(self, root_dir="."):
        self.root_dir = Path(root_dir)

    def get_confirmed_outcomes(self) -> dict[str, set[str]]:
        """
        Returns a mapping of match_id -> set of confirmed token_ids.
        If a match is confirmed but no tokens are specified, the set will be empty.
        """
        outcomes = defaultdict(set)

        # 1. strategy_outcomes.csv
        outcomes_path = self.root_dir / "logs" / "strategy_outcomes.csv"
        if outcomes_path.exists():
            try:
                df = pd.read_csv(outcomes_path)
                # Filter for settled/won outcomes
                settled_statuses = ["won", "resolved", "known", "settled", "win", "loss"]
                if "settlement_status" in df.columns:
                    df = df[df["settlement_status"].astype(str).str.lower().isin(settled_statuses)]
                
                if "match_id" in df.columns:
                    for _, row in df.iterrows():
                        m_id = str(row["match_id"])
                        if "token_id" in df.columns and pd.notna(row["token_id"]):
                            outcomes[m_id].add(str(row["token_id"]))
                        else:
                            if m_id not in outcomes:
                                outcomes[m_id] = set()
            except Exception as e:
                print(f"Error reading {outcomes_path}: {e}")

        # 2. shadow_outcomes_cache.json
        shadow_path = self.root_dir / "logs" / "shadow_outcomes_cache.json"
        if shadow_path.exists():
            try:
                with open(shadow_path, "r") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        # Support composite keys if they exist "match_id:token_id"
                        if ":" in str(k):
                            m_id, t_id = str(k).split(":", 1)
                            outcomes[m_id].add(t_id)
                        else:
                            m_id = str(k)
                            if m_id not in outcomes:
                                outcomes[m_id] = set()
                            # Support token info in value if available
                            if isinstance(v, dict):
                                if "token_id" in v:
                                    outcomes[m_id].add(str(v["token_id"]))
                                elif "tokens" in v and isinstance(v["tokens"], list):
                                    for t in v["tokens"]:
                                        outcomes[m_id].add(str(t))
            except Exception as e:
                print(f"Error reading {shadow_path}: {e}")

        # 3. settlement_shadow.csv (the shadow ledger)
        shadow_ledger = self.root_dir / "logs" / "settlement_shadow.csv"
        if shadow_ledger.exists():
            try:
                df = pd.read_csv(shadow_ledger)
                if "status" in df.columns:
                    valid_df = df[df["status"].str.upper().isin(["WIN", "LOSS"])]
                    for _, row in valid_df.iterrows():
                        m_id = str(row["match_id"])
                        if "token_id" in df.columns and pd.notna(row["token_id"]):
                            outcomes[m_id].add(str(row["token_id"]))
                        else:
                            if m_id not in outcomes:
                                outcomes[m_id] = set()
            except Exception as e:
                print(f"Error reading {shadow_ledger}: {e}")

        # 4. GAME_ENDED events in data_v2/dota_events
        event_dir = self.root_dir / "data_v2" / "dota_events"
        if event_dir.exists():
            for pfile in event_dir.glob("**/*.parquet"):
                try:
                    df = pd.read_parquet(pfile, columns=["event_type", "match_id"])
                    winners = df[df["event_type"] == "GAME_ENDED"]
                    for m_id in winners["match_id"].unique():
                        m_id_str = str(m_id)
                        if m_id_str not in outcomes:
                            outcomes[m_id_str] = set()
                except Exception as e:
                    print(f"Error reading {pfile}: {e}")

        # 5. opendota_outcomes.json
        opendota_path = self.root_dir / "logs" / "opendota_outcomes.json"
        if opendota_path.exists():
            try:
                with open(opendota_path, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k in data.keys():
                            m_id = str(k)
                            if m_id not in outcomes:
                                outcomes[m_id] = set()
            except Exception as e:
                print(f"Error reading {opendota_path}: {e}")

        return dict(outcomes)

class DataCounter:
    def __init__(self, root_dir="."):
        self.root_dir = Path(root_dir)

    def get_snapshot_counts(self):
        counts = {}
        snap_dir = self.root_dir / "data_v2" / "snapshots"
        if snap_dir.exists():
            for pfile in snap_dir.glob("**/*.parquet"):
                try:
                    df = pd.read_parquet(pfile, columns=["match_id"])
                    vc = df["match_id"].astype(str).value_counts()
                    for mid, count in vc.items():
                        counts[mid] = counts.get(mid, 0) + int(count)
                except Exception as e:
                    print(f"Error reading {pfile}: {e}")
        return counts

    def get_book_tick_counts(self):
        counts = {}
        book_dir = self.root_dir / "data_v2" / "book_ticks"
        if book_dir.exists():
            for pfile in book_dir.glob("**/*.parquet"):
                try:
                    df = pd.read_parquet(pfile, columns=["asset_id"])
                    vc = df["asset_id"].astype(str).value_counts()
                    for aid, count in vc.items():
                        counts[aid] = counts.get(aid, 0) + int(count)
                except Exception as e:
                    print(f"Error reading {pfile}: {e}")
        return counts

def filter_markets(markets, snap_counts, book_counts, confirmed_matches, confirmed_tokens):
    streamer_keywords = ["streamers", "streamer", "battle", "gorgc", "bald", "bulldog", "ns2", "nix", "recren", "miposh"]
    included = []
    report = []

    for m in markets:
        mid = str(m.get("dota_match_id") or "")
        yid = str(m.get("yes_token_id") or "")
        nid = str(m.get("no_token_id") or "")
        name = str(m.get("name") or "").lower()

        s_count = snap_counts.get(mid, 0)
        y_count = book_counts.get(yid, 0)
        n_count = book_counts.get(nid, 0)
        
        has_outcome = (mid in confirmed_matches) or (yid in confirmed_tokens) or (nid in confirmed_tokens)
        is_streamer = any(k in name for k in streamer_keywords)
        valid_mapping = mid and yid and nid and "PLACEHOLDER" not in yid and "PLACEHOLDER" not in nid

        exclusion_reason = None
        if not valid_mapping: exclusion_reason = "invalid_mapping"
        elif is_streamer: exclusion_reason = "is_streamer"
        elif s_count < 3: exclusion_reason = "insufficient_snapshots"
        elif y_count < 10 or n_count < 10: exclusion_reason = "insufficient_book_ticks"
        elif not has_outcome: exclusion_reason = "missing_outcome"

        row = {
            "market_id": m.get("market_id"),
            "dota_match_id": mid,
            "market_name": m.get("name"),
            "market_type": m.get("market_type"),
            "yes_token_id": yid,
            "no_token_id": nid,
            "steam_snapshot_count": s_count,
            "yes_book_tick_count": y_count,
            "no_book_tick_count": n_count,
            "has_outcome": has_outcome,
            "is_streamer": is_streamer,
            "included": exclusion_reason is None,
            "exclusion_reason": exclusion_reason
        }
        report.append(row)
        if exclusion_reason is None:
            included.append(m)

    return included, report

def generate_reports():
    from mapping import load_valid_mappings
    
    # 1. Load data
    agg = OutcomeAggregator()
    counter = DataCounter()
    
    outcomes = agg.get_confirmed_outcomes()
    snap_counts = counter.get_snapshot_counts()
    book_counts = counter.get_book_tick_counts()
    
    # Convert outcomes to confirmed matches and confirmed tokens
    confirmed_matches = set(outcomes.keys())
    confirmed_tokens = set()
    for tokens in outcomes.values():
        confirmed_tokens.update(tokens)
    
    # 2. Load mappings
    markets, _ = load_valid_mappings()
    print(f"Loaded {len(markets)} valid mappings.")
    
    # 3. Filter and report
    included, report = filter_markets(markets, snap_counts, book_counts, confirmed_matches, confirmed_tokens)
    
    # 4. Save results
    report_df = pd.DataFrame(report)
    report_df.to_csv("coverage_report.csv", index=False)
    print(f"Saved coverage_report.csv with {len(report)} entries.")
    
    included_df = pd.DataFrame(included)
    if not included_df.empty:
        # Select key columns for the analysis-ready dataset
        cols = ["market_id", "dota_match_id", "market_type", "yes_token_id", "no_token_id", "name"]
        # Only include columns that exist
        existing_cols = [c for c in cols if c in included_df.columns]
        included_df = included_df[existing_cols]
        included_df.to_csv("analysis_ready_markets.csv", index=False)
        print(f"Saved analysis_ready_markets.csv with {len(included)} entries.")
    else:
        # Create empty file with headers if no markets included
        pd.DataFrame(columns=["market_id", "dota_match_id", "market_type", "yes_token_id", "no_token_id", "name"]).to_csv("analysis_ready_markets.csv", index=False)
        print("No markets met inclusion criteria. Saved empty analysis_ready_markets.csv.")
    
    # 5. Export row data
    export_data_rows(included)

def export_data_rows(included_markets, root_dir="."):
    root = Path(root_dir)
    match_ids = {str(m.get("dota_match_id") or "") for m in included_markets if m.get("dota_match_id")}
    token_ids = set()
    for m in included_markets:
        if m.get("yes_token_id"):
            token_ids.add(str(m["yes_token_id"]))
        if m.get("no_token_id"):
            token_ids.add(str(m["no_token_id"]))

    if not match_ids:
        print("No match IDs to export.")
        return

    # Export snapshots
    print(f"Exporting snapshots for {len(match_ids)} matches...")
    snap_chunks = []
    snap_dir = root / "data_v2" / "snapshots"
    if snap_dir.exists():
        for pfile in snap_dir.glob("**/*.parquet"):
            try:
                df = pd.read_parquet(pfile)
                filtered = df[df["match_id"].astype(str).isin(match_ids)]
                if not filtered.empty:
                    snap_chunks.append(filtered)
            except Exception as e:
                print(f"Error reading {pfile}: {e}")
    
    if snap_chunks:
        pd.concat(snap_chunks).to_parquet("analysis_ready_snapshots.parquet", index=False)
        print(f"Saved analysis_ready_snapshots.parquet with {sum(len(c) for c in snap_chunks)} rows.")
    else:
        print("No matching snapshots found.")

    # Export book ticks
    print(f"Exporting book ticks for {len(token_ids)} tokens...")
    book_chunks = []
    book_dir = root / "data_v2" / "book_ticks"
    if book_dir.exists():
        for pfile in book_dir.glob("**/*.parquet"):
            try:
                df = pd.read_parquet(pfile)
                filtered = df[df["asset_id"].astype(str).isin(token_ids)]
                if not filtered.empty:
                    book_chunks.append(filtered)
            except Exception as e:
                print(f"Error reading {pfile}: {e}")
    
    if book_chunks:
        pd.concat(book_chunks).to_parquet("analysis_ready_book_ticks.parquet", index=False)
        print(f"Saved analysis_ready_book_ticks.parquet with {sum(len(c) for c in book_chunks)} rows.")
    else:
        print("No matching book ticks found.")

if __name__ == "__main__":
    generate_reports()
