import time
from poly_ws import BookStore

def test_bookstore_rolling_history():
    store = BookStore()
    
    book1 = store.update_direct("token_A", best_bid=0.40, best_ask=0.45)
    ns1 = book1["received_at_ns"]
    
    # ensure a gap in time
    time.sleep(0.01)
    
    book2 = store.update_direct("token_A", best_bid=0.50, best_ask=0.55)
    ns2 = book2["received_at_ns"]
    
    # 1. Exact match on ns1
    snap = store.get_snapshot_before("token_A", ns1)
    assert snap is not None
    assert snap["bid"] == 0.40
    assert snap["ask"] == 0.45
    import pytest
    assert snap["mid"] == pytest.approx(0.425)
    
    # 2. Middle time (between ns1 and ns2) -> should yield ns1
    mid_ns = ns1 + (ns2 - ns1) // 2
    snap_mid = store.get_snapshot_before("token_A", mid_ns)
    assert snap_mid is not None
    assert snap_mid["received_at_ns"] == ns1
    assert snap_mid["bid"] == 0.40
    
    # 3. Exact match on ns2
    snap_ns2 = store.get_snapshot_before("token_A", ns2)
    assert snap_ns2 is not None
    assert snap_ns2["received_at_ns"] == ns2
    assert snap_ns2["bid"] == 0.50
    
    # 4. Before history existed -> None
    snap_before = store.get_snapshot_before("token_A", ns1 - 1000)
    assert snap_before is None
