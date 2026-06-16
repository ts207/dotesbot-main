import re
import sys

with open('scripts/build_shadow_forward_daily_status.py', 'r') as f:
    content = f.read()

# 1. Add metrics
metrics_target = '        "validation_day_reject_reason": ""\n    }'
metrics_replacement = """        "validation_day_reject_reason": "",
        "future_book_relative_to_snapshot_count": 0,
        "future_book_relative_to_snapshot_would_enter_count": 0,
        "negative_book_age_count": 0,
        "negative_book_age_would_enter_count": 0,
        "max_negative_book_age_ms": 0,
        "excessive_skew_count": 0,
        "excessive_skew_would_enter_count": 0,
        "validation_eligible_false_detected": False,
        "backlog_replay_mode_detected": False,
        "monitor_process_down": False,
        "affected_monitor": ""
    }"""
content = content.replace(metrics_target, metrics_replacement)

# 2. Add crash detection (check logs/monitor_process_down.json)
crash_inject = """
    crash_path = Path("logs/monitor_process_down.json")
    if crash_path.exists():
        try:
            with open(crash_path, "r") as f:
                cdata = json.load(f)
                metrics["monitor_process_down"] = cdata.get("monitor_process_down", False)
                metrics["affected_monitor"] = cdata.get("affected_monitor", "")
        except: pass
"""
content = content.replace("    # Parse value attempts", crash_inject + "\n    # Parse value attempts")

# 3. Replace the entire parsing block for decisions
parse_target = """    # Latency and book age metrics
    decisions_log = Path("logs/value_v1_shadow_forward_decisions.jsonl")
    if decisions_log.exists():
        stream_delays = []
        book_ages = []
        with open(decisions_log, "r") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    row = json.loads(line)
                    if "stream_delay_s" in row:
                        stream_delays.append(float(row["stream_delay_s"]))
                    if "book_age_ms" in row:
                        book_ages.append(float(row["book_age_ms"]))
                except: pass
                
        if stream_delays:
            metrics["avg_stream_delay_s"] = round(np.mean(stream_delays), 2)
            metrics["p50_stream_delay_s"] = round(np.percentile(stream_delays, 50), 2)
            metrics["p95_stream_delay_s"] = round(np.percentile(stream_delays, 95), 2)
            metrics["max_stream_delay_s"] = round(np.max(stream_delays), 2)
        if book_ages:
            metrics["avg_book_age_ms"] = round(np.mean(book_ages), 2)
            metrics["p95_book_age_ms"] = round(np.percentile(book_ages, 95), 2)
            metrics["max_book_age_ms"] = round(np.max(book_ages), 2)"""

parse_replacement = """    # Latency and book age metrics
    stream_delays = []
    book_ages = []
    
    for log_path_str in ["logs/value_v1_shadow_forward_decisions.jsonl", "logs/market_disagreement_alpha_shadow_decisions.jsonl"]:
        decisions_log = Path(log_path_str)
        if decisions_log.exists():
            with open(decisions_log, "r") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        row = json.loads(line)
                        if "stream_delay_s" in row:
                            stream_delays.append(float(row["stream_delay_s"]))
                        
                        would_enter = False
                        if row.get("decision") in ["WOULD_ENTER", "WOULD_TRADE"]: would_enter = True
                        if row.get("alpha_would_enter") is True: would_enter = True
                        
                        if row.get("validation_eligible") is False:
                            metrics["validation_eligible_false_detected"] = True
                        if row.get("processing_mode") == "backlog_replay":
                            metrics["backlog_replay_mode_detected"] = True
                            
                        if "book_age_ms" in row:
                            b_age = float(row["book_age_ms"])
                            if b_age >= 0:
                                book_ages.append(b_age)
                            else:
                                metrics["negative_book_age_count"] += 1
                                metrics["max_negative_book_age_ms"] = min(metrics["max_negative_book_age_ms"], b_age)
                                if would_enter:
                                    metrics["negative_book_age_would_enter_count"] += 1
                                    
                        reason = row.get("reject_reason") or row.get("alpha_reject_reason") or row.get("reason") or ""
                        
                        if "future_book_relative_to_snapshot" in reason:
                            metrics["future_book_relative_to_snapshot_count"] += 1
                            if would_enter:
                                metrics["future_book_relative_to_snapshot_would_enter_count"] += 1
                                
                        if "excessive" in reason.lower() and "skew" in reason.lower():
                            metrics["excessive_skew_count"] += 1
                            if would_enter:
                                metrics["excessive_skew_would_enter_count"] += 1

                    except: pass
                    
    if stream_delays:
        metrics["avg_stream_delay_s"] = round(np.mean(stream_delays), 2)
        metrics["p50_stream_delay_s"] = round(np.percentile(stream_delays, 50), 2)
        metrics["p95_stream_delay_s"] = round(np.percentile(stream_delays, 95), 2)
        metrics["max_stream_delay_s"] = round(np.max(stream_delays), 2)
    if book_ages:
        metrics["avg_book_age_ms"] = round(np.mean(book_ages), 2)
        metrics["p95_book_age_ms"] = round(np.percentile(book_ages, 95), 2)
        metrics["max_positive_book_age_ms"] = round(np.max(book_ages), 2)"""

content = content.replace(parse_target, parse_replacement)

# 4. Add the hard stops to decision logic
stop_target = """    if decision not in ["STOP", "INVESTIGATE"]:
        if metrics["causality_violations"] > 0:"""
        
stop_replacement = """    if decision not in ["STOP", "INVESTIGATE"]:
        if metrics.get("monitor_process_down"):
            decision = "STOP"
            reject_reason = "monitor_process_down_or_restart_during_contaminated_day"
        elif metrics.get("negative_book_age_would_enter_count", 0) > 0 or metrics.get("future_book_relative_to_snapshot_would_enter_count", 0) > 0 or metrics.get("excessive_skew_would_enter_count", 0) > 0:
            decision = "STOP"
            reject_reason = "temporal_join_failure_and_log_mutation"
        elif metrics.get("backlog_replay_mode_detected") or metrics.get("validation_eligible_false_detected"):
            decision = "STOP"
            reject_reason = "backlog_replay_mode_in_validation_logs"
        elif metrics["causality_violations"] > 0:"""

content = content.replace(stop_target, stop_replacement)

with open('scripts/build_shadow_forward_daily_status.py', 'w') as f:
    f.write(content)
