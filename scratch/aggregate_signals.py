import pandas as pd
import glob
import os

def aggregate():
    files = glob.glob('logs/signals.csv*')
    all_df = []
    for f in files:
        if f.endswith('.bak'):
            try:
                df = pd.read_csv(f, names=range(85)) # Header-less bak files
                all_df.append(df)
                print(f"Loaded bak: {f} ({len(df)} rows)")
            except: pass
        elif f.endswith('.csv'):
            try:
                df = pd.read_csv(f)
                all_df.append(df)
                print(f"Loaded csv: {f} ({len(df)} rows)")
            except: pass
    
    if not all_df:
        print("No signal files found.")
        return
        
    combined = pd.concat(all_df, ignore_index=True)
    print(f"\nTOTAL SIGNALS ACROSS ALL DATA: {len(combined)}")
    
    # Analyze by tournament if possible
    if 'market_name' in combined.columns:
        counts = combined['market_name'].str.extract(r': (.*) vs').value_counts().head(10)
        print("\nTop Matches by Signal Count:")
        print(counts)

if __name__ == "__main__":
    aggregate()
