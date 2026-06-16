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
    finally:
        os.unlink(f_name)
