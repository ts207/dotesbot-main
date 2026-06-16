def cluster_episodes(signals, gap_seconds=180):
    """
    Groups a list of WOULD_ENTER signals into episodes.
    Assumes signals is sorted by decision_ts or poll_ts.
    
    Each signal must be a dict with:
      - match_id
      - market_id (optional, default '')
      - side (optional, default '')
      - rule_id (or policy_id)
      - ts_ns (timestamp in nanoseconds, e.g. decision_ts)
      
    Returns a list of episodes. Each episode is a list of signals.
    """
    if not signals:
        return []
        
    # Sort signals by timestamp just in case
    signals = sorted(signals, key=lambda x: x.get("ts_ns", 0))
    
    episodes = []
    current_episode = []
    
    def get_key(s):
        return (
            s.get("match_id", ""),
            s.get("market_id", ""),
            s.get("side", ""),
            s.get("rule_id", "UNKNOWN")
        )
        
    current_key = None
    last_ts_ns = 0
    
    gap_ns = gap_seconds * 1_000_000_000
    
    for s in signals:
        key = get_key(s)
        ts_ns = s.get("ts_ns", 0)
        
        if not current_episode:
            current_episode.append(s)
            current_key = key
            last_ts_ns = ts_ns
            continue
            
        is_same_key = (key == current_key)
        is_within_gap = (ts_ns - last_ts_ns) <= gap_ns
        
        if is_same_key and is_within_gap:
            current_episode.append(s)
        else:
            episodes.append(current_episode)
            current_episode = [s]
            current_key = key
            
        last_ts_ns = ts_ns
        
    if current_episode:
        episodes.append(current_episode)
        
    return episodes

def compute_episode_metrics(signals, gap_seconds=180):
    episodes = cluster_episodes(signals, gap_seconds)
    num_episodes = len(episodes)
    raw_signals = len(signals)
    
    if num_episodes == 0:
        return {
            "unique_episodes": 0,
            "raw_would_enter_signals": 0,
            "avg_signals_per_episode": 0,
            "median_signals_per_episode": 0,
            "max_signals_per_episode": 0,
            "top_1_episode_share": 0.0,
            "top_3_episode_share": 0.0,
            "top_5_episode_share": 0.0,
            "episode_gap_seconds": gap_seconds
        }
        
    episode_sizes = [len(ep) for ep in episodes]
    sorted_sizes = sorted(episode_sizes, reverse=True)
    
    import statistics
    avg_size = sum(episode_sizes) / num_episodes
    med_size = statistics.median(episode_sizes)
    max_size = max(episode_sizes)
    
    top_1 = sum(sorted_sizes[:1]) / raw_signals if raw_signals > 0 else 0
    top_3 = sum(sorted_sizes[:3]) / raw_signals if raw_signals > 0 else 0
    top_5 = sum(sorted_sizes[:5]) / raw_signals if raw_signals > 0 else 0
    
    return {
        "unique_episodes": num_episodes,
        "raw_would_enter_signals": raw_signals,
        "avg_signals_per_episode": round(avg_size, 2),
        "median_signals_per_episode": round(med_size, 2),
        "max_signals_per_episode": max_size,
        "top_1_episode_share": round(top_1, 3),
        "top_3_episode_share": round(top_3, 3),
        "top_5_episode_share": round(top_5, 3),
        "episode_gap_seconds": gap_seconds
    }
