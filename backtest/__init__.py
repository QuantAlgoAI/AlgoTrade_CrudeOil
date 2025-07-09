"""Convenience imports so callers can simply do `from backtest import db_pg_sync, backtest`.
This avoids `ImportError: cannot import name ...` when `__init__` is empty.
"""

from importlib import import_module as _imp

for _mod in ("db_pg_sync", "backtest"):
    globals()[_mod] = _imp(f"{__name__}.{_mod}")

del _imp, _mod
