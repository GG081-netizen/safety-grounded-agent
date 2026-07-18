"""Run one bounded command with the approved PostgreSQL test URL.

The URL is read from ~/.config/convagent/postgres_test.env and is never
printed or placed in the command line. No shell is used.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path


def _read_test_url(path: Path) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        if name.strip() != "CONVAGENT_POSTGRES_TEST_URL":
            continue
        values = shlex.split(raw_value.strip(), posix=True)
        if len(values) != 1 or not values[0].strip():
            raise RuntimeError("PostgreSQL test URL is malformed")
        return values[0].strip()
    raise RuntimeError("CONVAGENT_POSTGRES_TEST_URL is missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--as-application-url", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required")
    if args.timeout <= 0:
        parser.error("timeout must be positive")

    path = Path.home() / ".config" / "convagent" / "postgres_test.env"
    test_url = _read_test_url(path)
    environment = os.environ.copy()
    environment["CONVAGENT_POSTGRES_TEST_URL"] = test_url
    if args.as_application_url:
        environment["CONVAGENT_DATABASE_URL"] = test_url
    try:
        completed = subprocess.run(
            command,
            env=environment,
            check=False,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print("bounded PostgreSQL test command timed out")
        return 124
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
