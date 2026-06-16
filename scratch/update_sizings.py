import os

def main():
    env_path = ".env"
    with open(env_path, "r") as f:
        lines = f.readlines()

    updates = {
        "MAX_TRADE_USD": "70",
        "MAX_OPEN_USD_PER_MATCH": "70",
        "MAX_DAILY_DRAWDOWN_USD": "70",
        "PAPER_TRADE_SIZE_USD": "70",
        "MAX_TOTAL_LIVE_USD": "1000",
        "MAX_OPEN_POSITIONS": "10"
    }

    new_lines = []
    found = set()

    for line in lines:
        updated = False
        for k, v in updates.items():
            if line.startswith(f"{k}="):
                # keep existing comments if any
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
        
    print("Sizings updated to max capacity for $70.63 balance.")

if __name__ == "__main__":
    main()
