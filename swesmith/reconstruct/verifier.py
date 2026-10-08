"""Pure checks on hidden verifier tests and their control runs.

Nothing here touches containers or the LLM. Given the test source and the
status maps from control runs, these functions decide whether a candidate is
gradable and which tests form FAIL_TO_PASS.
"""

from __future__ import annotations

import ast
import re

from swesmith.harness.constants import TestStatus
from swesmith.harness.grading import test_failed

REQUIREMENT_ID = re.compile(r"\bREQ-\d+\b")
MIN_REQUIREMENTS = 3
MAX_REQUIREMENTS = 12


def validate_requirements(requirements: object) -> list[str]:
    """Check the design's requirement list shape. Returns problems, empty if valid."""
    if not isinstance(requirements, list):
        return ["requirements must be a list"]
    problems: list[str] = []
    if not MIN_REQUIREMENTS <= len(requirements) <= MAX_REQUIREMENTS:
        problems.append(
            f"expected {MIN_REQUIREMENTS}-{MAX_REQUIREMENTS} requirements, got {len(requirements)}"
        )
    seen: set[str] = set()
    for item in requirements:
        if not isinstance(item, dict):
            problems.append("each requirement must be an object with id and text")
            continue
        rid, text = item.get("id"), item.get("text")
        if not isinstance(rid, str) or not REQUIREMENT_ID.fullmatch(rid):
            problems.append(f"bad requirement id {rid!r}; use REQ-1, REQ-2, ...")
            continue
        if rid in seen:
            problems.append(f"duplicate requirement id {rid}")
        seen.add(rid)
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{rid} has no text")
    return problems


def requirement_ids(requirements: list[dict]) -> list[str]:
    return [item["id"] for item in requirements]


def check_requirement_mapping(source: str, requirement_list: list[str]) -> list[str]:
    """Every top-level test must cite a requirement, and every requirement must be covered."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"test file does not parse: {exc.msg} (line {exc.lineno})"]

    problems: list[str] = []
    tests = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    if not tests:
        problems.append("no top-level test_ functions")
    for node in tests:
        segment = ast.get_source_segment(source, node) or ""
        if not REQUIREMENT_ID.search(segment):
            problems.append(f"{node.name} cites no requirement id (REQ-n)")

    mentioned = set(REQUIREMENT_ID.findall(source))
    for rid in requirement_list:
        if rid not in mentioned:
            problems.append(f"{rid} is not covered by any test")
    unknown = sorted(mentioned - set(requirement_list))
    if unknown:
        problems.append(f"tests cite unknown requirement ids: {unknown}")
    return problems


def reference_gate_problems(reference_map: dict[str, str]) -> list[str]:
    """On the reference, every test must pass. Anything else means the test is wrong."""
    if not reference_map:
        return ["reference run produced no test results"]
    return [
        f"{test_id} is {status} on the reference"
        for test_id, status in sorted(reference_map.items())
        if status != TestStatus.PASSED.value
    ]


def derive_fail_to_pass(
    start_maps: list[dict[str, str]],
    reference_maps: list[dict[str, str]],
) -> tuple[list[str], list[str]]:
    """Return (FAIL_TO_PASS, problems) from the control runs.

    A test is FAIL_TO_PASS only if it fails or is missing on every start run
    and passes on every reference run. Any flakiness (differing statuses across
    runs of the same control) rejects the candidate.
    """
    if not start_maps or not reference_maps:
        return [], ["missing control runs"]

    test_ids = sorted({key for m in (*start_maps, *reference_maps) for key in m})
    fail_to_pass: list[str] = []
    problems: list[str] = []
    for test_id in test_ids:
        ref_values = {m.get(test_id) for m in reference_maps}
        start_values = {m.get(test_id) for m in start_maps}
        if len(ref_values) > 1 or len(start_values) > 1:
            problems.append(f"{test_id} has unstable status across control runs")
            continue
        ref_status = ref_values.pop()
        if ref_status != TestStatus.PASSED.value:
            problems.append(f"{test_id} is {ref_status} on the reference")
            continue
        if all(test_failed(test_id, m) for m in start_maps):
            fail_to_pass.append(test_id)
        else:
            problems.append(f"{test_id} passes on the stub, so it does not test the removed code")

    if not fail_to_pass and not problems:
        problems.append("no fail-to-pass tests")
    return fail_to_pass, problems
