"""
Base repository for SQLite storage in KZ Price Hunter 2.0.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


class BaseRepository:
    """
    Base repository providing connection management and utility methods.
    """
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_seq: list | tuple) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params_seq)

    def fetchone(self, sql: str, params: tuple | list | dict = ()) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple | list | dict = ()) -> List[Dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def commit(self) -> None:
        self.conn.commit()
