#!/usr/bin/env python3
"""Validate the clone-local Ruff contract used by standalone package CI."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

MINIMUM_RUFF = "0.8.0"
LINE_LENGTH = 99
SELECT = ("E", "F", "I", "UP", "B", "SIM")


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def ruff_requirements(pyproject: dict[str, Any]) -> list[str]:
    groups = pyproject.get("dependency-groups", {})
    optional = pyproject.get("project", {}).get("optional-dependencies", {})
    return [
        str(item)
        for item in [*groups.get("dev", []), *optional.get("dev", [])]
        if str(item).lower().startswith("ruff")
    ]


def sufficient_requirement(requirements: Sequence[str]) -> bool:
    minimum = version_tuple(MINIMUM_RUFF)
    for requirement in requirements:
        match = re.search(r"(?:>=|==|~=)\s*([0-9]+(?:\.[0-9]+)*)", requirement)
        if match and version_tuple(match.group(1)) >= minimum:
            return True
    return False


def missing_path_sources(root: Path, pyproject: dict[str, Any]) -> list[str]:
    sources = pyproject.get("tool", {}).get("uv", {}).get("sources", {})
    missing = []
    for row in sources.values():
        if not isinstance(row, dict) or not row.get("path"):
            continue
        source = str(row["path"])
        if not (root / source).resolve().exists():
            missing.append(source)
    return sorted(missing)


def main() -> int:
    root = Path.cwd()
    failures: list[str] = []
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"FAIL pyproject.toml: {exc}")
        return 1

    ruff = pyproject.get("tool", {}).get("ruff", {})
    lint = ruff.get("lint", {})
    if ruff.get("line-length") != LINE_LENGTH:
        failures.append(f"line-length must be {LINE_LENGTH}")
    if tuple(lint.get("select", [])) != SELECT:
        failures.append(f"select must be {list(SELECT)}")
    if failures:
        for failure in failures:
            print(f"FAIL Ruff config: {failure}")
    else:
        print("PASS Ruff config")

    requirements = ruff_requirements(pyproject)
    if not sufficient_requirement(requirements):
        failures.append(f"Ruff dev dependency must have a lower bound >= {MINIMUM_RUFF}")
        print(f"FAIL Ruff dependency: {requirements or 'absent'}")
    else:
        print(f"PASS Ruff dependency: {requirements[0]}")

    try:
        lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        failures.append(f"uv.lock is missing or invalid: {exc}")
        print(f"FAIL uv.lock: {exc}")
    else:
        locked = [
            str(row.get("version"))
            for row in lock.get("package", [])
            if isinstance(row, dict) and row.get("name") == "ruff" and row.get("version")
        ]
        if not locked or version_tuple(locked[0]) < version_tuple(MINIMUM_RUFF):
            failures.append("uv.lock does not contain a sufficient Ruff version")
            print(f"FAIL locked Ruff: {locked or 'absent'}")
        else:
            print(f"PASS locked Ruff: {locked[0]}")

    missing_sources = missing_path_sources(root, pyproject)
    if missing_sources:
        print(
            "SKIPPED lock freshness: standalone clone lacks sibling path source(s): "
            + ", ".join(missing_sources)
        )
    else:
        completed = subprocess.run(
            ["uv", "lock", "--check"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            message = detail[-1] if detail else f"uv lock --check exited {completed.returncode}"
            failures.append(message)
            print(f"FAIL lock freshness: {message}")
        else:
            print("PASS lock freshness")

    if failures:
        print(f"Ruff contract: FAIL ({len(failures)} finding(s))")
        return 1
    print("Ruff contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
