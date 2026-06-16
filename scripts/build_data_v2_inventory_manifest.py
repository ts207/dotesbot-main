#!/usr/bin/env python3
"""Compatibility entrypoint for the data_v2 inventory/audit phase."""
from __future__ import annotations

from audit_data_v2_event_store import main


if __name__ == "__main__":
    raise SystemExit(main())
