"""Failure containment for background loops.

One bad attempt or operation must not stop dispatch for every bot. Outages back off without
counting against an item, repeated item failures are quarantined, and a lost database still
crashes the process so its supervisor restarts it.
"""
from collections import defaultdict, deque
import time

from botocore.exceptions import ClientError, ConnectionError as StorageConnectionError, HTTPClientError
from fastapi import HTTPException
import httpx
import psycopg
from psycopg import errors

FAILURE_LIMIT = 3
# Retrying these is safe and expected; they are not a property of the item being driven.
TRANSIENT_DATABASE = (errors.TransactionRollback, errors.SerializationFailure, errors.DeadlockDetected,
                      errors.QueryCanceled, errors.LockNotAvailable)


def database_lost(exc):
    return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)) and not isinstance(exc, TRANSIENT_DATABASE)


def unavailable(exc):
    if isinstance(exc, ClientError):
        return exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0) >= 500
    if isinstance(exc, HTTPException):
        return exc.status_code in (503, 504)
    return isinstance(exc, (httpx.TransportError, StorageConnectionError, HTTPClientError, ConnectionError, TimeoutError) + TRANSIENT_DATABASE)


def describe(exc):
    """Owner-visible cause: the error class plus a known-safe detail, never a raw message."""
    if isinstance(exc, HTTPException) and isinstance(exc.detail, str):
        return f'{type(exc).__name__} {exc.status_code}: {exc.detail[:200]}'
    if isinstance(exc, ClientError):
        return f"{type(exc).__name__} {exc.response.get('Error', {}).get('Code', 'unknown')}"
    return type(exc).__name__


def diagnostic(exc):
    """Operator-only detail kept on the row for investigation."""
    return f'{type(exc).__name__}: {str(exc)[:500]}'


def backoff_seconds(failures):
    return min(60, 5 * 2 ** failures)


class Burst:
    """The same error across several distinct items in a short window points to a shared cause."""

    def __init__(self, items=3, window=60):
        self.items = items
        self.window = window
        self.seen = defaultdict(deque)

    def shared(self, exc, item_id):
        now = time.monotonic()
        seen = self.seen[describe(exc)]
        seen.append((now, item_id))
        while seen and seen[0][0] < now - self.window:
            seen.popleft()
        return len({item for _, item in seen}) >= self.items
