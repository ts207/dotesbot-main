import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from scratch.debug_my_8830206341 import entry_book, ns, params
book_age_ms = (ns - int(entry_book["received_at_ns"])) / 1_000_000
print(f"Age: {book_age_ms}, Max: {params['book_age_ms']}")
