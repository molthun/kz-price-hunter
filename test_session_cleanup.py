"""Tests for expired session cleanup and session management."""
import datetime
import unittest

from database import (
    init_db,
    get_connection,
    create_session,
    cleanup_expired_sessions,
)


class SessionCleanupTest(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_cleanup_expired_sessions(self):
        with get_connection() as conn:
            # Insert expired session
            past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).isoformat()
            future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).isoformat()
            conn.execute("INSERT OR REPLACE INTO users (id, username) VALUES (999999, 'session_tester')")
            conn.execute("INSERT OR REPLACE INTO sessions (token_hash, user_id, expires_at) VALUES (?, 999999, ?)",
                         ("hash_expired_test", past))
            conn.execute("INSERT OR REPLACE INTO sessions (token_hash, user_id, expires_at) VALUES (?, 999999, ?)",
                         ("hash_active_test", future))
            conn.commit()

        deleted = cleanup_expired_sessions()
        self.assertGreaterEqual(deleted, 1)

        with get_connection() as conn:
            row_expired = conn.execute("SELECT 1 FROM sessions WHERE token_hash = ?", ("hash_expired_test",)).fetchone()
            row_active = conn.execute("SELECT 1 FROM sessions WHERE token_hash = ?", ("hash_active_test",)).fetchone()
            self.assertIsNone(row_expired)
            self.assertIsNotNone(row_active)


if __name__ == "__main__":
    unittest.main()
