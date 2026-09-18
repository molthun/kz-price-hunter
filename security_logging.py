"""Redact credential-shaped values before logs leave the process.

This is defense in depth: provider bodies and raw transport errors must not be
logged. It is not a general personal-data anonymizer.
"""
import re

_PATTERNS = (
    # Telegram request paths, including truncated tokens in transport errors.
    (re.compile(r'(?i)(api\.telegram\.org/bot)[^\s/\"\'<>]+'), r'\1[REDACTED]'),
    (re.compile(r'\b\d{6,12}:[A-Za-z0-9_-]{20,}\b'), '[REDACTED]'),
    (re.compile(r'\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})\b'), '[REDACTED]'),
    (re.compile(r'(?i)(\b(?:authorization|proxy-authorization)[\"\']?\s*[:=]\s*[\"\']?(?:Bearer\s+|Basic\s+)?)[^\s,;\"\'}]+'), r'\1[REDACTED]'),
    (re.compile(r'(?i)(\b(?:[a-z_]*(?:api_key|bot_token|access_token|refresh_token)|token|secret|password|key|session_id)[\"\']?\s*[:=]\s*[\"\']?)[^\s&;,\"\'}]+'), r'\1[REDACTED]'),
)


def redact_secrets(value: str) -> str:
    text = str(value)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
