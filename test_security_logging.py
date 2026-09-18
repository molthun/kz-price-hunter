"""Synthetic-only logging tests; no app startup, config, database, or network."""
import io
import unittest
from unittest.mock import patch
from security_logging import redact_secrets
from log_manager import LogBuffer, StreamInterceptor


class SecurityLoggingTests(unittest.TestCase):
    def test_credential_formats(self):
        cases = [
            ('https://api.telegram.org/bot123456789:abcdefghijklmnopqrstuvwxyz0123456789/sendMessage', '123456789:abcdefghijklmnopqrstuvwxyz0123456789'),
            ('url?key=synthetic-api-key&other=1', 'synthetic-api-key'),
            ('Authorization: Bearer synthetic-bearer', 'synthetic-bearer'),
            ('{"openai_api_key": "synthetic-key"}', 'synthetic-key'),
            ('TELEGRAM_BOT_TOKEN=synthetic-token', 'synthetic-token'),
            ('error sk-proj-' + 'x' * 24, 'sk-proj-' + 'x' * 24),
            ('error AIza' + 'y' * 30, 'AIza' + 'y' * 30),
        ]
        for message, secret in cases:
            with self.subTest(message=message):
                safe = redact_secrets(message)
                self.assertNotIn(secret, safe)
                self.assertIn('[REDACTED]', safe)
        self.assertEqual(redact_secrets('HTTP 429: retry after 30 seconds'), 'HTTP 429: retry after 30 seconds')

    def test_split_writes_and_flush_never_forward_raw_tokens(self):
        terminal = io.StringIO(); buffer = LogBuffer()
        stream = StreamInterceptor(terminal)
        message = '[Telegram] https://api.telegram.org/bot123456789:abcdefghijklmnopqrstuvwxyz0123456789/sendMessage\n'
        with patch('log_manager.log_buffer', buffer):
            for char in message:
                self.assertEqual(stream.write(char), 1)
                stream.flush()
        for output in [terminal.getvalue(), buffer.export_text(), str(buffer.get_logs())]:
            self.assertNotIn('123456789:abcdefghijklmnopqrstuvwxyz0123456789', output)
            self.assertIn('[REDACTED]', output)
            self.assertIn('/sendMessage', output)

    def test_direct_buffer_and_source_are_redacted(self):
        buffer = LogBuffer()
        buffer.add('password=synthetic-password', source='token=synthetic-source')
        for output in [buffer.export_text(), str(buffer.get_logs())]:
            self.assertNotIn('synthetic-password', output)
            self.assertNotIn('synthetic-source', output)


if __name__ == '__main__':
    unittest.main()
