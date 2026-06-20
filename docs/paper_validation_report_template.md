# MODEL_VALUE_EDGE Paper Validation Report

This document serves as the promotion and validation report template for the `MODEL_VALUE_EDGE` strategy before deploying real capital. Fill out this template for each validation run.

## Validation Overview

- **Trained Model Version:** 
- **Trained Dataset (e.g. expanded_sideaware_highmed_112):** 
- **Validation Period Start:** YYYY-MM-DD
- **Validation Period End:** YYYY-MM-DD
- **Execution Mode:** [Research Paper | Live-Parity Paper | Shadow-Live]

---

## 1. Paper Validation Runs Checklist

Use the three paper validation phases outlined in the rollout plan:

- [ ] **1. Research Paper Mode (`PAPER_MODE=research`)**
  - **Purpose:** Run the strategy to collect and log all would-trade signals and examine policy rejects.
  - **Status:** [Pending / In Progress / Completed]
  - **Notes:** 

- [ ] **2. Live-Parity Paper Mode (`PAPER_MODE=live_parity`)**
  - **Purpose:** Only enter paper positions that would pass the live policy gates (i.e. simulating strict live trading on paper).
  - **Status:** [Pending / In Progress / Completed]
  - **Notes:** 

- [ ] **3. Shadow-Live Mode (`LIVE_TRADING=true`, `ENABLE_REAL_LIVE_TRADING=false`, `PAPER_MODE=shadow_live`)**
  - **Purpose:** Verify the dry-live policy / execution pipeline (balance checks, sizing, DB persistence) without sending actual orders.
  - **Status:** [Pending / In Progress / Completed]
  - **Notes:** 

---

## 2. Validation Metrics Summary

Fill in the summary statistics computed from the `strategy_signals.csv`, `paper_attempts.csv`, and position stores.

| Metric | Target / Minimum | Achieved Value | Status (Pass/Fail) |
| :--- | :---: | :---: | :---: |
| **Total Paper Trades** | $\ge 100$ | | |
| **Settlement ROI (%)** | $> 0.0\%$ | | |
| **900s CLV (Cents)** | $> 0.0$ | | |
| **1200s CLV (Cents)** | $> 0.0$ | | |
| **Last 25 Trades Return** | Non-negative / mildly negative | | |
| **Policy Bypasses Detected** | 0 | | |
| **Mapping Errors Encountered** | 0 | | |

---

## 3. Top / Single-Match Profit Sensitivity

Check if the strategy's profitability depends heavily on a single trade or match.

- **Largest Positive Match PnL (USD):** 
- **Strategy PnL excluding Largest Match (USD):** 
- **Did any mapping mismatch or pricing anomaly occur?** [Yes / No]
- **Describe any anomalies or outliers:**

---

## 4. Promotion Criteria Checklist

Before moving `MODEL_VALUE_EDGE` to real live trading, all of the following gates must be marked **Pass**:

- [ ] **Data Quality Gate:** No mapping-error wins or incorrect side attribution.
- [ ] **Sufficient Sample size:** At least 100 paper trades executed.
- [ ] **Positive Edge / CLV:** Settlement ROI and 900s or 1200s CLV are positive.
- [ ] **Stability Gate:** The latest 25 trades are non-negative or only mildly negative, indicating stable current performance.
- [ ] **No Policy Bypasses:** Signals successfully evaluated through `evaluate_policy` and matched `execution_policy.py` rules.
- [ ] **Fail-Closed default:** `strategies/model_value_edge.yaml` `enabled_real_live` remains `false` until explicitly promoted.

---

## 5. Paper Trade Logs Detail (Sample Template)

For each trade, record the following fields in the validation dataset or report:

| Match ID | Token ID | Side | Game Time (s) | Ask | Bid | Spread | Model Prob | Edge | Confirm Age (s) | Entry Price | Settlement | PnL | 30s Mid | 120s Mid | 900s Mid | 1200s Mid | CLV 900s | CLV 1200s | Fold/Date |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| *Example* | *tok_abc* | *YES* | *920* | *0.55* | *0.52* | *0.03* | *0.72* | *0.17* | *14.5* | *0.55* | *Won* | *0.45* | *0.56* | *0.59* | *0.62* | *0.70* | *0.07* | *0.15* | *Fold_1* |
| | | | | | | | | | | | | | | | | | | | |

---

## Sign-off & Promotion Decision

- **Approved for Deployment:** [YES / NO]
- **Date:** YYYY-MM-DD
- **Approver Signature:** 
