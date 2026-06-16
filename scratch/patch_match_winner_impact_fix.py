import sys

def patch_main():
    with open("main.py", "r") as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines):
        if "expected_move = abs(sig[\"magnitude\"])" in line:
            # Fix indentation based on context
            if i > 1700: # The second occurrence is at line 1739
                lines[i] = "                expected_move = abs(sig[\"magnitude\"])\n"
        elif "if m.get(\"market_type\") == \"MATCH_WINNER\":" in line:
            if i > 1700:
                lines[i] = "                if m.get(\"market_type\") == \"MATCH_WINNER\":\n"
        elif "try:" in line:
            if i > 1700 and "try:" in lines[i]:
                 # Only if it's the specific try block
                 if "p_next = float(m.get(\"p_next_yes\") or 0.5)" in lines[i+1]:
                     lines[i] = "                    try:\n"
        elif "p_next = float(m.get(\"p_next_yes\") or 0.5)" in line:
            if i > 1700:
                lines[i] = "                        p_next = float(m.get(\"p_next_yes\") or 0.5)\n"
        elif "p_next = max(0.01, min(0.99, p_next))" in line:
            if i > 1700:
                lines[i] = "                        p_next = max(0.01, min(0.99, p_next))\n"
        elif "score_yes = int(m.get(\"series_score_yes\") or 0)" in line:
            if i > 1700:
                lines[i] = "                        score_yes = int(m.get(\"series_score_yes\") or 0)\n"
        elif "score_no = int(m.get(\"series_score_no\") or 0)" in line:
            if i > 1700:
                lines[i] = "                        score_no = int(m.get(\"series_score_no\") or 0)\n"
        elif "gnum = int(m.get(\"current_game_number\") or m.get(\"game_number\") or 1)" in line:
            if i > 1700:
                lines[i] = "                        gnum = int(m.get(\"current_game_number\") or m.get(\"game_number\") or 1)\n"
        elif "if gnum == 1:" in line:
            if i > 1700:
                lines[i] = "                        if gnum == 1:\n"
        elif "sensitivity = 2 * p_next * (1 - p_next)" in line:
            if i > 1700:
                lines[i] = "                            sensitivity = 2 * p_next * (1 - p_next)\n"
        elif "elif gnum == 2:" in line:
            if i > 1700:
                lines[i] = "                        elif gnum == 2:\n"
        elif "sensitivity = (1 - p_next) if score_yes >= score_no else p_next" in line:
            if i > 1700:
                lines[i] = "                            sensitivity = (1 - p_next) if score_yes >= score_no else p_next\n"
        elif "else:" in line:
            if i > 1700 and "sensitivity = 1.0" in lines[i+1]:
                lines[i] = "                        else:\n"
        elif "sensitivity = 1.0" in line:
            if i > 1700:
                lines[i] = "                            sensitivity = 1.0\n"
        elif "except (TypeError, ValueError):" in line:
            if i > 1700:
                lines[i] = "                    except (TypeError, ValueError):\n"
        elif "sensitivity = 0.5" in line:
            if i > 1700:
                lines[i] = "                        sensitivity = 0.5\n"
        elif "expected_move *= sensitivity" in line:
            if i > 1700:
                lines[i] = "                expected_move *= sensitivity\n"
        elif "_bm_fair = min(_exec_mid + expected_move, 0.95)" in line:
            if i > 1700:
                lines[i] = "                _bm_fair = min(_exec_mid + expected_move, 0.95)\n"

    with open("main.py", "w") as f:
        f.writelines(lines)
        
patch_main()
