import os

def main():
    env_path = ".env"
    if not os.path.exists(env_path):
        if os.path.exists(".env.example"):
            with open(".env.example", "r") as f:
                lines = f.readlines()
        else:
            lines = []
    else:
        with open(env_path, "r") as f:
            lines = f.readlines()

    # We need to ensure LIVE_TRADING=true and ENABLE_REAL_LIVE_TRADING=true
    new_lines = []
    live_trading_set = False
    enable_real_set = False
    
    has_key = False
    has_rpc = False

    for line in lines:
        if line.startswith("LIVE_TRADING="):
            new_lines.append("LIVE_TRADING=true\n")
            live_trading_set = True
        elif line.startswith("ENABLE_REAL_LIVE_TRADING="):
            new_lines.append("ENABLE_REAL_LIVE_TRADING=true\n")
            enable_real_set = True
        else:
            new_lines.append(line)
            
        if line.startswith("POLY_PRIVATE_KEY=") and len(line.strip()) > 20:
            has_key = True
        if line.startswith("POLYGON_RPC_URL=") and len(line.strip()) > 20:
            has_rpc = True

    if not live_trading_set:
        new_lines.append("LIVE_TRADING=true\n")
    if not enable_real_set:
        new_lines.append("ENABLE_REAL_LIVE_TRADING=true\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)
        
    print("Environment armed.")
    if not has_key:
        print("WARNING: POLY_PRIVATE_KEY appears missing or empty in .env")
    if not has_rpc:
        print("WARNING: POLYGON_RPC_URL appears missing. Default/public RPCs will be slow!")

if __name__ == "__main__":
    main()
