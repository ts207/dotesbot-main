import os

def main():
    env_path = ".env"
    with open(env_path, "r") as f:
        lines = f.readlines()

    updates = {
        "MIN_LAG": "0.05",
        "MIN_EXECUTABLE_EDGE": "0.003",
        "MAX_SPREAD": "0.15",
        "TRADE_EVENTS": "POLL_ULTRA_LATE_FIGHT_FLIP,POLL_FIGHT_SWING,POLL_STRUCTURAL_DOMINANCE,POLL_VALUE_DISAGREEMENT,POLL_RAPID_STOMP,POLL_LATE_FIGHT_FLIP,POLL_DECISIVE_STOMP,POLL_COMEBACK_RECOVERY,POLL_MAJOR_COMEBACK_RECOVERY"
    }

    new_lines = []
    found = set()

    for line in lines:
        updated = False
        for k, v in updates.items():
            if line.startswith(f"{k}="):
                comment = ""
                if "#" in line:
                    comment = "  " + line[line.find("#"):]
                new_lines.append(f"{k}={v}{comment}")
                if not comment.endswith("\n"):
                    new_lines[-1] += "\n"
                found.add(k)
                updated = True
                break
        if not updated:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in found:
            new_lines.append(f"{k}={v}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)
        
    print("Strategy optimized with backtest-winning parameters.")

if __name__ == "__main__":
    main()
