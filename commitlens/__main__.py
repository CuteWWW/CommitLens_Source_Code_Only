"""让 `python -m commitlens` 可用。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
