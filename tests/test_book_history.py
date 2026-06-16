import time
from poly_ws import BookStore

def test_bookstore_get_snapshot_before_returns_latest_prior_snapshot():
    store = BookStore()
    
    store.update_direct("T1", best_bid=0.40, best_ask=0.45)
    t1 = 1000
    store.book_history["T1"][-1]["received_at_ns"] = t1
    
    store.update_direct("T1", best_bid=0.50, best_ask=0.55)
    t2 = 2000
    store.book_history["T1"][-1]["received_at_ns"] = t2
    
    snap = store.get_snapshot_before("T1", 1500)
    assert snap is not None
    assert snap["bid"] == 0.40

def test_bookstore_get_snapshot_before_returns_none_if_no_prior_snapshot():
    store = BookStore()
    store.update_direct("T1", best_bid=0.40, best_ask=0.45)
    t1 = 1000
    store.book_history["T1"][-1]["received_at_ns"] = t1
    
    snap = store.get_snapshot_before("T1", 500)
    assert snap is None

def test_event_pre_price_uses_snapshot_before_event_not_current_book():
    store = BookStore()
    
    book1 = store.update_direct("T1", best_bid=0.40, best_ask=0.45)
    t1 = book1["received_at_ns"]
    
    time.sleep(0.01)
    
    store.update_direct("T1", best_bid=0.80, best_ask=0.85)
    
    snap = store.get_snapshot_before("T1", t1 + 1000)
    assert snap["bid"] == 0.40
    assert store.get("T1")["best_bid"] == 0.80
