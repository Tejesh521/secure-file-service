"""Block until the database accepts connections (used by compose and CI).

Usage: python scripts/wait_for_db.py [--timeout 60]
Reads DATABASE_URL from the environment.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from sqlalchemy import create_engine, text

from app.core.config import normalize_database_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    url = normalize_database_url(url)
    engine = create_engine(url, connect_args={"connect_timeout": 3} if url.startswith("postgresql") else {})
    deadline = time.monotonic() + args.timeout
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print(f"database ready after {attempt} attempt(s)")
            return 0
        except Exception as exc:  # any failure means "not yet"
            if time.monotonic() >= deadline:
                print(f"database not reachable after {args.timeout}s: {type(exc).__name__}", file=sys.stderr)
                return 1
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
