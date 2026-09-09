"""SWE-smith's local task and evaluation constants.

These values intentionally live in the project instead of being imported from
the external benchmark harness, which eagerly loads execution backends that
are not used here. SWE-smith executes tasks through Singularity.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class ResolvedStatus(Enum):
    NO = "RESOLVED_NO"
    PARTIAL = "RESOLVED_PARTIAL"
    FULL = "RESOLVED_FULL"


class TestStatus(Enum):
    FAILED = "FAILED"
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    XFAIL = "XFAIL"


FAIL_TO_PASS = "FAIL_TO_PASS"
FAIL_TO_FAIL = "FAIL_TO_FAIL"
PASS_TO_PASS = "PASS_TO_PASS"
PASS_TO_FAIL = "PASS_TO_FAIL"

RUN_EVALUATION_LOG_DIR = Path("logs/run_evaluation")
LOG_REPORT = "report.json"
LOG_INSTANCE = "run_instance.log"
LOG_TEST_OUTPUT = "test_output.txt"

APPLY_PATCH_FAIL = ">>>>> Patch Apply Failed"
APPLY_PATCH_PASS = ">>>>> Applied Patch"
TESTS_TIMEOUT = ">>>>> Tests Timed Out"


def get_resolution_status(report: dict[str, dict[str, list[str]]]) -> str:
    """Classify an evaluation report using SWE-bench's resolution rules."""
    f2p = report[FAIL_TO_PASS]
    p2p = report[PASS_TO_PASS]
    f2p_total = len(f2p["success"]) + len(f2p["failure"])
    p2p_total = len(p2p["success"]) + len(p2p["failure"])
    f2p_rate = len(f2p["success"]) / f2p_total if f2p_total else 0
    p2p_rate = len(p2p["success"]) / p2p_total if p2p_total else 1
    if f2p_rate == 1 and p2p_rate == 1:
        return ResolvedStatus.FULL.value
    if 0 < f2p_rate < 1 and p2p_rate == 1:
        return ResolvedStatus.PARTIAL.value
    return ResolvedStatus.NO.value
