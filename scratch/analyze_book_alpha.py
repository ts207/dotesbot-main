import pandas as pd
import numpy as np

# Load book moves log
try:
    df = pd.read_csv("logs/book_moves.csv")
except:
    print("Log not found.")
    exit()

# Filter for moves that were skipped due to 'steam_contradicts'
# These are our 'Alpha' candidates - the market moved, but Steam hadn't updated yet.
alpha_candidates = df[df['trade_skip_reason'] == 'steam_contradicts'].copy()

print(f"Total Alpha Candidates (Steam Contradicts): {len(alpha_candidates)}")

# In a real environment, we'd want to see if the Steam lead 'caught up' to the book move direction.
# For now, let's look at the magnitude and spread of these signals.
print("\n--- Alpha Candidate Stats ---")
cols = ['magnitude', 'spread', 'book_age_ms', 'radiant_lead']
print(alpha_candidates[cols].describe())

# Check for 'Clean' Alpha: tight spread, fresh book, significant magnitude
clean_alpha = alpha_candidates[
    (alpha_candidates['spread'] <= 0.03) & 
    (alpha_candidates['book_age_ms'] < 1000) & 
    (abs(alpha_candidates['magnitude']) >= 0.02)
]

print(f"\nClean Alpha Candidates (Spread <= 0.03, Age < 1s, Mag >= 0.02): {len(clean_alpha)}")
if not clean_alpha.empty:
    print(clean_alpha[['market_name', 'direction', 'magnitude', 'radiant_lead']].head(10))

