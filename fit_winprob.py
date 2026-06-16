#!/usr/bin/env python3
"""Fit a calibrated win-probability model P(win | gold_lead, minute[, draft]) from
the OpenDota pro-match training set (logs/opendota_pro.json).

Each match contributes one labeled row per game-minute:
    (gold_lead_at_minute, minute) -> radiant_win

Validation is GROUPED BY MATCH (rows in a match share the label, so a random
split would leak). Produces:
  - an empirical 2D calibration table (lead_bin x minute_bin) -> win-rate + n
    => this is the `fair` lookup the value bot will use
  - a smooth logistic model for comparison / AUC / log-loss
  - a draft-contribution test (does draft help beyond live net worth?)
Saves the artifact to logs/winprob_model.json.
"""
import json, math
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.metrics import log_loss, roc_auc_score, brier_score_loss

PRO = "logs/opendota_pro.json"
OUT = "logs/winprob_model.json"

# ---- 1) build training rows -------------------------------------------------
cache = json.load(open(PRO))
G, M, Y, GRP = [], [], [], []          # gold_lead, minute, label, match-id (group)
DRAFT = []                              # per-row radiant draft score (filled later)
match_meta = []                        # (mid, radiant_win, picks_bans)
for gi, (mid, m) in enumerate(cache.items()):
    rw = m.get("radiant_win")
    ga = m.get("radiant_gold_adv") or []
    if rw is None or len(ga) < 6:
        continue
    match_meta.append((mid, int(bool(rw)), m.get("picks_bans") or []))
    for minute, gold in enumerate(ga):
        if gold is None:
            continue
        G.append(float(gold)); M.append(float(minute)); Y.append(int(bool(rw))); GRP.append(gi)
G = np.array(G); M = np.array(M); Y = np.array(Y); GRP = np.array(GRP)
print(f"rows={len(Y)}  matches={len(set(GRP))}  radiant_winrate={Y.mean():.3f}")

# ---- 2) empirical 2D calibration table -------------------------------------
lead_edges = [-1e9, -10000, -5000, -2000, -1000, 0, 1000, 2000, 5000, 10000, 1e9]
min_edges  = [0, 10, 15, 20, 25, 30, 35, 40, 1e9]
lead_lab = ["<-10k","-10..-5k","-5..-2k","-2..-1k","-1..0","0..1k","1..2k","2..5k","5..10k","10k+"]
min_lab  = ["0-10","10-15","15-20","20-25","25-30","30-35","35-40","40+"]
li = np.digitize(G, lead_edges[1:-1])
mi = np.digitize(M, min_edges[1:-1])
table = {}   # f"{li}_{mi}" -> [winrate, n]
print("\nEMPIRICAL WIN-RATE  (rows=minute-bin, cols=lead-bin)   [n in parens]")
header = "min\\lead    " + "".join(f"{l:>11}" for l in lead_lab)
print(header)
for r in range(len(min_lab)):
    cells = []
    for c in range(len(lead_lab)):
        mask = (li == c) & (mi == r)
        n = int(mask.sum())
        if n >= 8:
            wr = float(Y[mask].mean())
            table[f"{c}_{r}"] = [round(wr, 4), n]
            cells.append(f"{wr:.2f}({n:>4})")
        else:
            cells.append(f"  -  ({n:>4})")
    print(f"{min_lab[r]:<10}" + "".join(f"{x:>11}" for x in cells))

# ---- 3) smooth logistic model + grouped CV ---------------------------------
def feats(g, m):
    gk = g / 1000.0
    return np.column_stack([
        gk,
        gk * m,
        gk / np.sqrt(m + 1.0),
        np.sign(g) * (gk ** 2),
        m,
    ])
X = feats(G, M)
gkf = GroupKFold(n_splits=5)
oof = np.zeros(len(Y))
for tr, te in gkf.split(X, Y, GRP):
    mdl = LogisticRegression(max_iter=2000, C=1.0)
    mdl.fit(X[tr], Y[tr])
    oof[te] = mdl.predict_proba(X[te])[:, 1]
print("\n--- LOGISTIC (match-grouped 5-fold OOF) ---")
print(f"  log-loss : {log_loss(Y, oof):.4f}")
print(f"  brier    : {brier_score_loss(Y, oof):.4f}")
print(f"  AUC      : {roc_auc_score(Y, oof):.4f}")
acc = ((oof >= .5).astype(int) == Y).mean()
base = ((G > 0).astype(int) == Y).mean()
print(f"  accuracy : {acc:.4f}   (baseline sign(gold)={base:.4f})")

# calibration of OOF preds
print("  calibration (pred-bucket -> actual):")
for lo in np.arange(0, 1.0, 0.1):
    mk = (oof >= lo) & (oof < lo + 0.1)
    if mk.sum() >= 30:
        print(f"    {lo:.1f}-{lo+0.1:.1f}: pred~{oof[mk].mean():.3f} actual {Y[mk].mean():.3f}  n={int(mk.sum())}")

# ---- 4) draft contribution test --------------------------------------------
# shrunk per-hero side-winrate from the data (radiant pick => +win signal).
hero_w = {}  # hero_id -> [radiant_wins_contrib, radiant_n, dire_wins, dire_n]
for mid, rw, pbs in match_meta:
    for pb in pbs:
        if not pb.get("is_pick"):
            continue
        h = pb.get("hero_id"); team = pb.get("team")  # 0=radiant,1=dire
        if h is None:
            continue
        d = hero_w.setdefault(h, [0, 0, 0, 0])
        if team == 0:
            d[0] += rw; d[1] += 1
        else:
            d[2] += (1 - rw); d[3] += 1
PRIOR = 0.5; K = 20.0
def hero_score(h):
    d = hero_w.get(h)
    if not d:
        return 0.0
    rs = (d[0] + PRIOR * K) / (d[1] + K) if d[1] else PRIOR   # P(radiant win | hero on radiant)
    ds = (d[2] + PRIOR * K) / (d[3] + K) if d[3] else PRIOR
    return (rs - 0.5) - (ds - 0.5)
# per-match draft score = sum radiant hero scores - sum dire hero scores
mdraft = {}
for gi, (mid, rw, pbs) in enumerate(match_meta):
    s = 0.0
    for pb in pbs:
        if not pb.get("is_pick"): continue
        h = pb.get("hero_id")
        sc = hero_score(h)
        s += sc if pb.get("team") == 0 else -sc
    mdraft[gi] = s
draft_arr = np.array([mdraft.get(g, 0.0) for g in GRP])

# does draft help EARLY (<10 min, small lead) beyond gold?
early = (M < 10)
Xe = np.column_stack([feats(G, M), draft_arr])
oof_d = np.zeros(len(Y))
for tr, te in gkf.split(Xe, Y, GRP):
    mdl = LogisticRegression(max_iter=2000).fit(Xe[tr], Y[tr])
    oof_d[te] = mdl.predict_proba(Xe[te])[:, 1]
print("\n--- DRAFT contribution (note: hero scores from same set => optimistic) ---")
print(f"  EARLY (<10min) log-loss  no-draft={log_loss(Y[early], oof[early]):.4f}  +draft={log_loss(Y[early], oof_d[early]):.4f}")
print(f"  ALL          log-loss  no-draft={log_loss(Y, oof):.4f}  +draft={log_loss(Y, oof_d):.4f}")

# ---- 5) save artifact ------------------------------------------------------
artifact = {
    "kind": "winprob_v1",
    "n_rows": int(len(Y)), "n_matches": int(len(set(GRP))),
    "lead_edges": lead_edges, "min_edges": min_edges,
    "lead_lab": lead_lab, "min_lab": min_lab,
    "table": table,   # "leadidx_minidx" -> [winrate, n]
    "logistic_oof": {"log_loss": float(log_loss(Y, oof)), "auc": float(roc_auc_score(Y, oof)), "acc": float(acc)},
}
json.dump(artifact, open(OUT, "w"), indent=0)
print(f"\nsaved -> {OUT}  ({len(table)} populated cells)")
