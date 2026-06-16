from unified_storage.bulk import rows_to_table
from unified_storage.schemas import ALL_SCHEMAS
from unified_storage.writers import BatchWriter


def test_value_attempts_schema_registered():
    assert "value_attempts" in ALL_SCHEMAS
    assert BatchWriter("value_attempts", batch_rows=1)


def test_value_attempts_rows_coerce_to_table():
    table = rows_to_table(
        [
            {
                "timestamp_utc": "2026-06-15T23:37:25.974+00:00",
                "received_at_ns": "1781566645974159439",
                "signal_id": "",
                "match_id": "8853618398",
                "would_trade": "False",
                "reject_reason": "one_sided_book_missing_ask",
                "direction": "dire",
                "side": "NO",
                "token_id": "T",
                "fair_price": "",
                "ask": "",
                "edge": "",
                "lead": "-29346",
                "game_time_sec": "1711",
                "elo_diff": "",
                "book_age_ms": "5717",
                "sized_usd": "",
            }
        ],
        "value_attempts",
        source_file="value_attempts.csv",
    )

    assert table.num_rows == 1
    assert table.schema == ALL_SCHEMAS["value_attempts"]
    row = table.to_pylist()[0]
    assert row["reject_reason"] == "one_sided_book_missing_ask"
    assert row["lead"] == -29346
    assert row["book_age_ms"] == 5717.0
