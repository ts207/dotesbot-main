import json
import os
import subprocess
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path

def run_script(script_name):
    try:
        res = subprocess.run(["python3", script_name], capture_output=True, text=True, timeout=30)
        return res.stdout
    except Exception as e:
        return f"Error running {script_name}: {e}"

def main():
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    
    # 1. Run all scripts to capture outputs for the markdown report
    health_out = run_script("scripts/check_shadow_forward_health.py")
    rej_out = run_script("scripts/analyze_recent_value_rejections.py")
    val_out = run_script("scripts/analyze_value_v1_shadow_forward_results.py")
    alpha_out = run_script("scripts/analyze_market_disagreement_alpha_shadow.py")
    
    # 2. Parse daily metrics directly
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Initialize defaults
    metrics = {
        "date": date_str,
        "feed_active_hours": 0.0,
        "VALUE_evaluations": 0,
        "VALUE_WOULD_ENTER_count": 0,
        "VALUE_reject_distribution": {},
        "near_threshold_pressure_score": 0.0,
        "deep_discount_candidate_count": 0,
        "market_lag_candidate_count": 0,
        "causality_violations": 0,
        "stale_book_WOULD_ENTER_count": 0,
        "degraded_source_WOULD_ENTER_count": 0,
        "config_drift_detected": False,
        "decision": "INVESTIGATE",
        "validation_day_usable": False,
        "validation_day_reject_reason": "",
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
    }

    # Extract health status
    health_lines = health_out.strip().split("\n")
    health_dict = {}
    for line in health_lines:
        if "=" in line:
            k, v = line.split("=", 1)
            health_dict[k.strip()] = v.strip()

    # Determine config drift and causality violations
    if health_dict.get("SAFETY_STATUS") != "PASS":
        metrics["causality_violations"] = 1 # Proxy for failing safety
    
    if "config drift" in health_out.lower():
        metrics["config_drift_detected"] = True

    emergency_stop = False
    stop_path = Path("reports/shadow_forward_emergency_stop_required.json")
    if stop_path.exists():
        with open(stop_path, "r") as f:
            try:
                stop_data = json.load(f)
                if stop_data.get("emergency_stop_recommended"):
                    emergency_stop = True
            except: pass


    crash_path = Path("logs/monitor_process_down.json")
    if crash_path.exists():
        try:
            with open(crash_path, "r") as f:
                cdata = json.load(f)
                metrics["monitor_process_down"] = cdata.get("monitor_process_down", False)
                metrics["affected_monitor"] = cdata.get("affected_monitor", "")
        except: pass

    # Parse value attempts
    val_log = Path("logs/value_attempts.csv")
    if val_log.exists():
        df = pd.read_csv(val_log)
        if not df.empty:
            df['timestamp_utc'] = pd.to_datetime(df['timestamp_utc'])
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            df_today = df[df['timestamp_utc'] >= today]
            
            if not df_today.empty:
                metrics["VALUE_evaluations"] = len(df_today)
                time_span = (df_today['timestamp_utc'].max() - df_today['timestamp_utc'].min()).total_seconds() / 3600.0
                metrics["feed_active_hours"] = round(time_span, 2)
                
                rejects = df_today[df_today['would_trade'] == False]
                metrics["VALUE_reject_distribution"] = rejects['reject_reason'].value_counts().to_dict()
                metrics["VALUE_WOULD_ENTER_count"] = len(df_today[df_today['would_trade'] == True])
                
                # Near threshold pressure: % of edge_too_small that were within 0.010
                edge_too_small = df_today[df_today['reject_reason'] == 'edge_too_small']
                if not edge_too_small.empty:
                    # from our previous script logic: VALUE_MIN_EDGE is around 0.15
                    # since we don't have .env here directly, we just proxy it
                    # or we can just read the value from df if available, or parse rej_out
                    pass

    # Parse rej_out for our specific splits from the previous script
    if "0.000 - 0.005 below threshold" in rej_out:
        # We can extract the pressure score roughly
        lines = rej_out.split("\n")
        total_near = 0
        total_edge_rejects = 0
        in_bucket_section = False
        for line in lines:
            if "Edge Too Small Bucketing" in line:
                in_bucket_section = True
                continue
            if in_bucket_section and "below threshold" in line:
                try:
                    count = int(line.split()[-1])
                    total_edge_rejects += count
                    if "0.000 - 0.005" in line or "0.005 - 0.010" in line:
                        total_near += count
                except:
                    pass
            elif in_bucket_section and line.strip() == "":
                in_bucket_section = False
        if total_edge_rejects > 0:
            metrics["near_threshold_pressure_score"] = round(total_near / total_edge_rejects, 3)

    if "deep_discount_high_edge_leader_candidate" in rej_out:
        for line in rej_out.split("\n"):
            if "deep_discount_high_edge_leader_candidate" in line:
                try:
                    metrics["deep_discount_candidate_count"] = int(line.split()[-1])
                except:
                    pass

    # Latency and book age metrics
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
                        ts = row.get("decision_wall_time_utc")
                        if not ts and row.get("poll_ts"):
                            ts = datetime.fromtimestamp(row["poll_ts"]/1e9, tz=timezone.utc).strftime("%Y-%m-%d")
                        if ts and not ts.startswith(date_str):
                            continue
                            
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
        metrics["max_positive_book_age_ms"] = round(np.max(book_ages), 2)

    # Decision Logic
    # CONTINUE: no safety violations, logs updating, no config drift
    # INVESTIGATE: near_threshold_pressure_score > 5%, logs stale, rejection distribution changes sharply
    # STOP: causality violation, stale-book WOULD_ENTER, config drift
    
    decision = "CONTINUE"
    reject_reason = ""

    if emergency_stop:
        decision = "STOP"
        reject_reason = "emergency_stop_recommended"
        
    integrity_path = Path("reports/shadow_log_integrity.json")
    if integrity_path.exists():
        try:
            with open(integrity_path, "r") as f:
                integ_data = json.load(f)
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if (
                    (integ_data.get("log_mutated") or integ_data.get("mutation_detected"))
                    and integ_data.get("contaminated_date") == today_str
                ):
                    decision = "INVESTIGATE"
                    reject_reason = integ_data.get("excluded_reason", "log_mutated")
        except: pass
        
    elif metrics["config_drift_detected"]:
        decision = "STOP"
        reject_reason = "config_drift"
    elif health_dict.get("SHADOW_HEALTH") != "PASS" or health_dict.get("VALIDATION_CLOCK") == "NOT_STARTED":
        decision = "INVESTIGATE"
        reject_reason = "primary_or_secondary_shadow_logs_missing_or_immature"
    elif metrics["near_threshold_pressure_score"] > 0.05:
        decision = "INVESTIGATE"
        reject_reason = "high_near_threshold_pressure"
    
    if metrics.get("monitor_process_down"):
        decision = "STOP"
        reject_reason = "monitor_process_down_or_restart_during_contaminated_day"
    elif metrics.get("negative_book_age_would_enter_count", 0) > 0 or metrics.get("future_book_relative_to_snapshot_would_enter_count", 0) > 0 or metrics.get("excessive_skew_would_enter_count", 0) > 0:
        decision = "STOP"
        reject_reason = "temporal_join_failure_and_log_mutation"
    elif metrics.get("backlog_replay_mode_detected") or metrics.get("validation_eligible_false_detected"):
        decision = "STOP"
        reject_reason = "backlog_replay_mode_in_validation_logs"
    elif decision not in ["STOP", "INVESTIGATE"]:
        if metrics["causality_violations"] > 0:
            decision = "STOP"
            reject_reason = "safety_violation_causality"
        elif metrics["stale_book_WOULD_ENTER_count"] > 0:
            decision = "STOP"
            reject_reason = "stale_book_would_enter"
        elif metrics["degraded_source_WOULD_ENTER_count"] > 0:
            decision = "STOP"
            reject_reason = "degraded_source_would_enter"
        
    metrics["decision"] = decision
    metrics["validation_day_usable"] = (decision == "CONTINUE")
    metrics["validation_day_reject_reason"] = reject_reason if not metrics["validation_day_usable"] else None

    # Write JSON
    json_path = reports_dir / "shadow_forward_daily_status.json"
    with open(json_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # Validation Ledger Logic
    ledger_path = Path("logs/validation_ledger.json")
    ledger = {
        "calendar_days_observed": [],
        "usable_validation_days": [],
        "last_usable_validation_day": "none"
    }
    if ledger_path.exists():
        try:
            with open(ledger_path, "r") as f:
                ledger = json.load(f)
        except:
            pass
            
    if date_str not in ledger.get("calendar_days_observed", []):
        ledger.setdefault("calendar_days_observed", []).append(date_str)
        
    if metrics["validation_day_usable"]:
        if date_str not in ledger.get("usable_validation_days", []):
            ledger.setdefault("usable_validation_days", []).append(date_str)
            ledger["last_usable_validation_day"] = date_str
            
    with open(ledger_path, "w") as f:
        json.dump(ledger, f, indent=2)
        
    ledger_summary = (
        f"Usable validation days so far: {len(ledger.get('usable_validation_days', []))}\n"
        f"Calendar days observed: {len(ledger.get('calendar_days_observed', []))}\n"
        f"Last usable validation day: {ledger.get('last_usable_validation_day', 'none')}\n"
        f"Current reject reason: {reject_reason if not metrics['validation_day_usable'] else 'none'}\n"
    )

    # Write Markdown
    md_path = reports_dir / "shadow_forward_daily_status.md"
    with open(md_path, "w") as f:
        f.write(f"# Shadow Forward Daily Status: {date_str}\n\n")
        f.write(f"```text\n{ledger_summary}```\n\n")
        f.write("## 1. Daily Metrics\n\n```json\n")
        f.write(json.dumps(metrics, indent=2))
        f.write("\n```\n\n")
        f.write("## 2. Health Checker Output\n\n```text\n")
        f.write(health_out)
        f.write("\n```\n\n")
        f.write("## 3. Rejection Diagnostics\n\n```text\n")
        f.write(rej_out)
        f.write("\n```\n\n")
        f.write("## 4. VALUE v1 Shadow Results\n\n```text\n")
        f.write(val_out)
        f.write("\n```\n\n")
        f.write("## 5. Alpha Shadow Results\n\n```text\n")
        f.write(alpha_out)
        f.write("\n```\n\n")

    print(f"Daily status report generated at {md_path}")
    print(f"Decision: {decision}")

if __name__ == '__main__':
    main()
