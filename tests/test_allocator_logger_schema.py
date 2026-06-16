import pytest
from storage import AllocatorLogger
import tempfile
import os
import csv

def test_allocator_logger_headers():
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
        f_name = f.name
        
    try:
        logger = AllocatorLogger(filename=f_name)
        
        with open(f_name, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader)
            
        assert "candidate_count" in headers
        assert "blocked_count" in headers
        assert "blocked_strategies" in headers
        assert "winner_edge_type" in headers
        assert "winner_target_horizon" in headers
        assert "winner_expected_hold_sec" in headers
        assert "block_reason" in headers
    finally:
        os.unlink(f_name)
