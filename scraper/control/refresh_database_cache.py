#!/usr/bin/env python3
"""Refresh the materialized aggregates used by the Database views."""

from __future__ import annotations

import os
import sys
import time

import psycopg2


VIEWS = ("database_totals_cache", "database_state_summaries_cache")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("refresh-database-cache: DATABASE_URL is not set", file=sys.stderr)
        return 2

    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        for view in VIEWS:
            started = time.monotonic()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}")
            except psycopg2.Error as error:
                print(
                    f"refresh-database-cache: {view} failed: {error}",
                    file=sys.stderr,
                )
                return 1
            elapsed = time.monotonic() - started
            print(f"refresh-database-cache: {view} in {elapsed:.1f}s")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
