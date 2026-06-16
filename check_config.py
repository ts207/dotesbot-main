from __future__ import annotations

import sys

from runtime_config import load_config


def _format_value(value) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def main() -> int:
    try:
        cfg = load_config(validate_real_live=True)
    except RuntimeError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        try:
            cfg = load_config(validate_real_live=False)
        except RuntimeError:
            return 2
        exit_code = 2
    else:
        exit_code = 0

    headers = [
        "setting",
        "runtime value",
        "source",
        "safe_for_paper",
        "safe_for_dry_live",
        "safe_for_real_live",
    ]
    rows = [
        [
            item.setting,
            _format_value(item.value),
            item.source,
            str(item.safe_for_paper).lower(),
            str(item.safe_for_dry_live).lower(),
            str(item.safe_for_real_live).lower(),
        ]
        for item in cfg.values
    ]
    widths = [
        max(len(str(row[i])) for row in [headers, *rows])
        for i in range(len(headers))
    ]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
