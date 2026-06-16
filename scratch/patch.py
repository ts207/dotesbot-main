import re
import sys

def patch_file(filepath, is_alpha=False):
    with open(filepath, 'r') as f:
        content = f.read()

    # 1. Imports and Main Argument Parsing
    import_target = "import math\nfrom collections import deque, Counter" if not is_alpha else "import math\nfrom pathlib import Path\nimport csv"
    import_replacement = import_target.replace("import math", "import math\nimport argparse")
    content = content.replace(import_target, import_replacement)

    main_target = "def main():\n"
    if "def main():" in content:
        main_replacement = """def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-backlog", action="store_true", help="Unsafe for validation. Replay historical backlog.")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and exit.")
    args = parser.parse_args()
    
    if args.dry_run:
        print("Dry run successful. Config validated.")
        return

    processing_mode = "backlog_replay" if args.replay_backlog else "live_tail"
    validation_eligible = not args.replay_backlog\n"""
        content = content.replace(main_target, main_replacement)

    # 2. Disable .gz replay
    if not is_alpha:
        gz_target_re = r'    import gzip\n    logs_dir = REPO_ROOT / "logs"\n    archive_paths = sorted\(logs_dir\.glob\("raw_snapshots\.csv\.\*\.gz"\)\)\n.*?# --- START MAIN LOOP ---'
        content = re.sub(gz_target_re, '    snap_file_offset = 0\n    # --- START MAIN LOOP ---', content, flags=re.DOTALL)
    else:
        # In alpha, there is no .gz replay! Just add snap_file_offset
        main_loop_str = "# --- START MAIN LOOP ---"
        if main_loop_str not in content:
            # Just put it before while True:
            content = content.replace("    while True:", "    snap_file_offset = 0\n    while True:")

    # 3. Snapshot Reading Loop & Temporal Logic
    snap_old = """            if snapshot_path.exists():
                with open(snapshot_path, "r") as f:
                    f.readline()
                    for line in f:
                        row = next(csv.reader([line]))"""
                        
    snap_new = """            if snapshot_path.exists():
                current_snap_size = snapshot_path.stat().st_size
                if snap_file_offset == 0:
                    with open(snapshot_path, "r") as f:
                        f.readline()
                        if processing_mode == "live_tail":
                            f.seek(0, 2)
                        snap_file_offset = f.tell()
                if current_snap_size > snap_file_offset:
                    with open(snapshot_path, "r") as f:
                        f.seek(snap_file_offset)
                        for line in f:
                            row = next(csv.reader([line]))"""
    
    content = content.replace(snap_old, snap_new)
    
    # 4. Advance offset at the end of loop
    # We must find the sleep and inject offset update before it
    content = content.replace("            time.sleep(1)", "            if snapshot_path.exists():\n                with open(snapshot_path, \"r\") as f:\n                    f.seek(0, 2)\n                    snap_file_offset = f.tell()\n            time.sleep(1)")

    # 5. Inject temporal fields into the JSON payload
    if not is_alpha:
        # For value_v1, we need to inject into `ValueEngine.evaluate` mapping, or into the logged dictionary
        payload_target = """                                "stream_delay_s": game.get("stream_delay_s", 0.0),
                                "book_age_ms": book_age_ms,"""
        
        payload_replacement = """                                "stream_delay_s": game.get("stream_delay_s", 0.0),
                                "book_age_ms": book_age_ms,
                                "processing_mode": processing_mode,
                                "validation_eligible": validation_eligible,"""
        content = content.replace(payload_target, payload_replacement)
        
        # Calculate wall time logic right before payload
        time_logic_target = """                            book_received_at_ns = book_entry.get("received_at_ns", 0)
                            book_age_ms = int((ns - book_received_at_ns) / 1_000_000) if book_received_at_ns > 0 else 0"""
                            
        time_logic_replacement = """                            book_received_at_ns = book_entry.get("received_at_ns", 0)
                            decision_wall_time_ns = time.time_ns()
                            
                            if processing_mode == "live_tail":
                                book_age_ms = int((decision_wall_time_ns - book_received_at_ns) / 1_000_000) if book_received_at_ns > 0 else 0
                                if decision_wall_time_ns < book_received_at_ns:
                                    decision = "WOULD_REJECT"
                                    reason = "future_book_relative_to_wall_clock"
                                elif decision_wall_time_ns < ns:
                                    decision = "WOULD_REJECT"
                                    reason = "future_snapshot_relative_to_wall_clock"
                                elif abs(book_received_at_ns - ns) > shadow_config.get("max_feed_skew_ns", 15_000_000_000):
                                    decision = "WOULD_REJECT"
                                    reason = "excessive_snapshot_book_skew"
                            else:
                                book_age_ms = int((ns - book_received_at_ns) / 1_000_000) if book_received_at_ns > 0 else 0
                                if book_received_at_ns > ns:
                                    decision = "WOULD_REJECT"
                                    reason = "future_book_relative_to_snapshot\"\"\""""
                                    
        content = content.replace(time_logic_target, time_logic_replacement.replace('\"\"\"', ''))
        
    else: # is_alpha
        # For alpha, temporal logic is evaluated directly before jsonl writing
        # Find evaluate_alpha_rules and inject
        eval_call_target = "v1_results = engine.evaluate(game, markets[match_id], book_store)"
        eval_call_replacement = "v1_results = engine.evaluate(game, markets[match_id], book_store)\n                        decision_wall_time_ns = time.time_ns()"
        content = content.replace(eval_call_target, eval_call_replacement)
        
        args_target = "decisions = evaluate_alpha_rules(game, markets[match_id], book_store, v1_results, history)"
        args_replacement = "decisions = evaluate_alpha_rules(game, markets[match_id], book_store, v1_results, history, processing_mode, validation_eligible, decision_wall_time_ns)"
        content = content.replace(args_target, args_replacement)
        
        def_target = "def evaluate_alpha_rules(game, mapping, book_store, v1_results, history):"
        def_replacement = "def evaluate_alpha_rules(game, mapping, book_store, v1_results, history, processing_mode='backlog_replay', validation_eligible=False, decision_wall_time_ns=None):"
        content = content.replace(def_target, def_replacement)

        time_logic_target = """    book_age_sec = book_age_ms / 1000.0 if book_age_ms > 0 else 0.0
    snapshot_age_sec = (time.time_ns() - cur_ns) / 1e9"""
    
        time_logic_replacement = """    if decision_wall_time_ns is None: decision_wall_time_ns = time.time_ns()
    if processing_mode == "live_tail":
        book_age_ms = int((decision_wall_time_ns - book_ns) / 1_000_000) if book_ns > 0 else 0
        feed_skew_status = "OK"
        if decision_wall_time_ns < book_ns:
            feed_skew_status = "future_book_relative_to_wall_clock"
        elif decision_wall_time_ns < cur_ns:
            feed_skew_status = "future_snapshot_relative_to_wall_clock"
        elif abs(book_ns - cur_ns) > 15_000_000_000:
            feed_skew_status = "excessive_snapshot_book_skew"
    else:
        book_age_ms = int((cur_ns - book_ns) / 1_000_000) if book_ns > 0 else 0
        if book_ns > cur_ns:
            feed_skew_status = "future_book_relative_to_snapshot"
            
    book_age_sec = book_age_ms / 1000.0 if book_age_ms > 0 else 0.0
    snapshot_age_sec = (time.time_ns() - cur_ns) / 1e9"""
        content = content.replace(time_logic_target, time_logic_replacement)
        
        # Remove old skew checks
        skew_block_target = """    snapshot_book_skew_sec = (cur_ns - book_ns) / 1e9
    
    if abs(snapshot_book_skew_sec) > 15.0:
        feed_skew_status = "EXCESSIVE_SKEW"
    elif snapshot_book_skew_sec > 0:
        feed_skew_status = "BOOK_NEWER_THAN_SNAPSHOT"
    elif snapshot_book_skew_sec < 0:
        feed_skew_status = "SNAPSHOT_NEWER_THAN_BOOK"
    else:
        feed_skew_status = "OK\"\"\""""
        
        content = content.replace(skew_block_target.replace('\"\"\"', ''), "    snapshot_book_skew_sec = (cur_ns - book_ns) / 1e9")
        
        # Add to JSON output
        payload_target = """        "passes_gettoplive_guard": passes_gettoplive_guard
    })"""
        payload_replacement = """        "passes_gettoplive_guard": passes_gettoplive_guard,
        "processing_mode": processing_mode,
        "validation_eligible": validation_eligible
    })"""
        content = content.replace(payload_target, payload_replacement)

    with open(filepath, 'w') as f:
        f.write(content)

patch_file('scripts/run_value_v1_shadow_forward_monitor.py', is_alpha=False)
patch_file('scripts/run_market_disagreement_alpha_shadow.py', is_alpha=True)
