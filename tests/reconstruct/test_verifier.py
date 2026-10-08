import pytest

from swesmith.reconstruct.verifier import (
    check_requirement_mapping,
    derive_fail_to_pass,
    reference_gate_problems,
    validate_requirements,
)

GOOD_TESTS = '''def test_sums_positive_items():
    # REQ-1
    assert compute_total([1, -2, 3], 2) == 8


def test_zero_scale():
    """REQ-2: scale of zero gives zero."""
    assert compute_total([1, 2], 0) == 0
'''


def _reqs(*ids):
    return [{"id": rid, "text": f"behavior {rid}"} for rid in ids]


def test_mapping_accepts_full_coverage():
    assert check_requirement_mapping(GOOD_TESTS, ["REQ-1", "REQ-2"]) == []


def test_mapping_flags_uncited_test_and_uncovered_requirement():
    source = "def test_a():\n    assert True\n"
    problems = check_requirement_mapping(source, ["REQ-1"])
    assert any("test_a cites no requirement" in p for p in problems)
    assert any("REQ-1 is not covered" in p for p in problems)


def test_mapping_flags_unknown_ids_and_syntax_errors():
    unknown = check_requirement_mapping(GOOD_TESTS, ["REQ-1"])
    assert any("unknown requirement ids" in p for p in unknown)
    broken = check_requirement_mapping("def test_a(:\n", ["REQ-1"])
    assert broken and "does not parse" in broken[0]


def test_mapping_requires_at_least_one_test():
    problems = check_requirement_mapping("X = 1\n", ["REQ-1"])
    assert "no top-level test_ functions" in problems


@pytest.mark.parametrize(
    "requirements, fragment",
    [
        ("nope", "must be a list"),
        (_reqs("REQ-1", "REQ-2"), "expected 3-12 requirements"),
        (_reqs("REQ-1", "REQ-1", "REQ-2"), "duplicate requirement id REQ-1"),
        ([{"id": "R1", "text": "x"}, *_reqs("REQ-2", "REQ-3")], "bad requirement id"),
        ([{"id": "REQ-1", "text": " "}, *_reqs("REQ-2", "REQ-3")], "REQ-1 has no text"),
    ],
)
def test_requirement_shape_problems(requirements, fragment):
    problems = validate_requirements(requirements)
    assert any(fragment in p for p in problems), problems


def test_valid_requirements_have_no_problems():
    assert validate_requirements(_reqs("REQ-1", "REQ-2", "REQ-3")) == []


def test_reference_gate_requires_every_test_to_pass():
    assert reference_gate_problems({"t::a": "PASSED", "t::b": "PASSED"}) == []
    problems = reference_gate_problems({"t::a": "PASSED", "t::b": "FAILED"})
    assert problems == ["t::b is FAILED on the reference"]
    assert reference_gate_problems({}) == ["reference run produced no test results"]


def test_fail_to_pass_is_failing_on_stub_and_passing_on_reference():
    start = [{"t::a": "FAILED", "t::b": "PASSED"}, {"t::a": "FAILED", "t::b": "PASSED"}]
    ref = [{"t::a": "PASSED", "t::b": "PASSED"}] * 4
    f2p, problems = derive_fail_to_pass(start, ref)
    assert f2p == ["t::a"]
    assert problems == ["t::b passes on the stub, so it does not test the removed code"]


def test_missing_on_stub_counts_as_failing():
    # A collection error on the stub leaves no result lines at all.
    start = [{}, {}]
    ref = [{"t::a": "PASSED"}, {"t::a": "PASSED"}]
    f2p, _ = derive_fail_to_pass(start, ref)
    assert f2p == ["t::a"]


def test_flaky_control_rejects_candidate():
    start = [{"t::a": "FAILED"}, {"t::a": "PASSED"}]
    ref = [{"t::a": "PASSED"}] * 4
    f2p, problems = derive_fail_to_pass(start, ref)
    assert f2p == []
    assert any("unstable status" in p for p in problems)


def test_failing_reference_rejects_test():
    start = [{"t::a": "FAILED"}] * 2
    ref = [{"t::a": "FAILED"}] * 4
    f2p, problems = derive_fail_to_pass(start, ref)
    assert f2p == []
    assert problems == ["t::a is FAILED on the reference"]


def test_no_control_runs_is_rejected():
    assert derive_fail_to_pass([], [{"t::a": "PASSED"}]) == ([], ["missing control runs"])


def test_all_pass_on_stub_is_rejected_as_no_fail_to_pass():
    start = [{"t::a": "PASSED"}] * 2
    ref = [{"t::a": "PASSED"}] * 4
    f2p, problems = derive_fail_to_pass(start, ref)
    assert f2p == []
    assert any("passes on the stub" in p for p in problems)
