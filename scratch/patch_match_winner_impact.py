import sys

def patch_main():
    with open("main.py", "r") as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines):
        if "_bm_fair = min(_exec_mid + abs(sig[\"magnitude\"]) * 1.0, 0.95)" in line:
            # We want to replace this logic to include sensitivity scaling
            patch = """
                            # Scale magnitude for MATCH_WINNER (Series) markets
                            expected_move = abs(sig["magnitude"])
                            if m.get("market_type") == "MATCH_WINNER":
                                try:
                                    p_next = float(m.get("p_next_yes") or 0.5)
                                    p_next = max(0.01, min(0.99, p_next))
                                    score_yes = int(m.get("series_score_yes") or 0)
                                    score_no = int(m.get("series_score_no") or 0)
                                    gnum = int(m.get("current_game_number") or m.get("game_number") or 1)
                                    if gnum == 1:
                                        sensitivity = 2 * p_next * (1 - p_next)
                                    elif gnum == 2:
                                        sensitivity = (1 - p_next) if score_yes >= score_no else p_next
                                    else:
                                        sensitivity = 1.0
                                except (TypeError, ValueError):
                                    sensitivity = 0.5
                                expected_move *= sensitivity
                            
                            _bm_fair = min(_exec_mid + expected_move, 0.95)
"""
            lines[i] = patch

    with open("main.py", "w") as f:
        f.writelines(lines)
        
patch_main()
