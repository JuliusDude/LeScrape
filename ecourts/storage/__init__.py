"""
ecourts.storage - Persistent SQLite storage with WAL mode, normalized schema, and atomic operations.
"""
from ecourts.storage.db import Database

__all__ = ["Database"]
