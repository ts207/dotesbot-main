import os
import json
import unittest
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import storage_v2
import live_state
from live_executor import LiveExecutor

class TestLiveRiskDurable(unittest.TestCase):
    def setUp(self):
        self.test_db_path = "logs/test_state_v2.sqlite"
        storage_v2.DEFAULT_DB_PATH = self.test_db_path
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    def tearDown(self):
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    @patch("live_executor.MAX_TOTAL_LIVE_USD", 10.0)
    def test_persistence_and_budget_cap(self):
        # 1. Create a state with $9.50 already spent
        initial_state = {
            "total_submitted_usd": 9.50,
            "total_filled_usd": 8.0,
            "open_positions": 1,
            "daily_realized_pnl_usd": 0.0,
            "last_reset_date": "2024-01-01",
            "submitted_match_sides": {},
            "submitted_match_usd": {},
            "submitted_family_usd": {},
            "updated_at_ns": 123456789
        }
        storage = storage_v2.StorageV2()
        
        from datetime import datetime, timezone
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        storage.save_daily_budget(today_str, initial_state)

        # 2. Initialize LiveExecutor and verify it loads the state
        executor = LiveExecutor()
        self.assertEqual(executor.total_submitted_usd, 9.50)
        self.assertEqual(executor.total_filled_usd, 8.0)
        self.assertEqual(executor.open_positions, 1)
        self.assertEqual(executor.remaining_budget(), 0.50)  # patched MAX_TOTAL_LIVE_USD=10.0

        # 3. Explicit check
        self.assertTrue(executor.total_submitted_usd + 1.0 > 10.0)

    def test_save_on_update(self):
        executor = LiveExecutor()
        executor.total_submitted_usd = 2.0
        executor.total_filled_usd = 1.5
        executor.open_positions = 0
        
        # Manually trigger save (in real code this happens after order submission)
        live_state.save_live_state(executor.total_submitted_usd, executor.total_filled_usd, executor.open_positions)
        
        # Verify db content
        storage = storage_v2.StorageV2()
        from datetime import datetime, timezone
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = storage.load_daily_budget(today_str)
        
        self.assertIsNotNone(data)
        self.assertEqual(data["total_submitted_usd"], 2.0)
        self.assertEqual(data["total_filled_usd"], 1.5)

if __name__ == "__main__":
    unittest.main()
