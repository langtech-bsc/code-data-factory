"""Filesystem-backed phase status used by local and Slurm orchestration."""

from __future__ import annotations

import json
import os
import tempfile
import traceback

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from swesmith.constants import LOG_DIR_STATUS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def status_path(phase: str, repo: str) -> Path:
    """Return the stable status path for one phase/repository pair."""
    return LOG_DIR_STATUS / phase / f"{repo}.json"


def write_status(phase: str, repo: str, payload: dict[str, Any]) -> Path:
    """Atomically publish a phase status record on the shared filesystem."""
    destination = status_path(phase, repo)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def run_statused(phase: str, repo: str, function: Callable[[], dict[str, Any] | None]) -> dict[str, Any]:
    """Run one unit, publish success/failure status, and return the record."""
    started = _now()
    try:
        result = function() or {}
        record = {
            "phase": phase,
            "repo": repo,
            "status": "success",
            "started_at": started,
            "finished_at": _now(),
            **result,
        }
    except Exception as exc:  # noqa: BLE001 - status must survive worker errors
        record = {
            "phase": phase,
            "repo": repo,
            "status": "failed",
            "started_at": started,
            "finished_at": _now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    write_status(phase, repo, record)
    return record


def phase_failed(phase: str, repos: list[str]) -> bool:
    """Return whether any requested unit has a non-success status."""
    for repo in repos:
        path = status_path(phase, repo)
        if not path.exists():
            return True
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("status") != "success":
                return True
        except (OSError, json.JSONDecodeError):
            return True
    return False
