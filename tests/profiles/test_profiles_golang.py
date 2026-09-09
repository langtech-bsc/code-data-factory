import pytest
import subprocess

from swesmith.profiles.golang import GoProfile, Gin3c12d2a8
from unittest.mock import patch, mock_open


# Minimal concrete GoProfile for generic tests
def make_dummy_go_profile():
    class DummyGoProfile(GoProfile):
        owner = "dummy"
        repo = "dummyrepo"
        commit = "deadbeefcafebabe"

        @property
        def recipe(self):
            return "FROM golang:1.20\nRUN echo hello"

    return DummyGoProfile()


def test_go_profile_log_parser_basic():
    profile = make_dummy_go_profile()
    log = """
=== RUN   TestFoo
--- PASS: TestFoo (0.01s)
=== RUN   TestBar
--- FAIL: TestBar (0.02s)
=== RUN   TestBaz
--- SKIP: TestBaz (0.00s)
PASS
ok      github.com/dummy/dummyrepo    0.030s
"""
    result = profile.log_parser(log)
    assert result["TestFoo"] == "PASSED"
    assert result["TestBar"] == "FAILED"
    assert result["TestBaz"] == "SKIPPED"


def test_go_profile_log_parser_no_matches():
    profile = make_dummy_go_profile()
    log = """
=== RUN   TestFoo
Some random output
PASS
ok      github.com/dummy/dummyrepo    0.030s
"""
    result = profile.log_parser(log)
    assert result == {}


def test_go_profile_log_parser_edge_cases():
    profile = make_dummy_go_profile()
    # Empty log
    assert profile.log_parser("") == {}
    # Whitespace only
    assert profile.log_parser("   \n  \t  \n") == {}
    # Malformed lines
    log = """
--- PASS: TestFoo
--- FAIL: TestBar (0.02s
--- SKIP: TestBaz (0.00s)
"""
    result = profile.log_parser(log)
    assert "TestBar" in result
    assert "TestBaz" in result


def test_go_profile_log_parser_multiple_tests():
    profile = make_dummy_go_profile()
    log = """
--- PASS: TestHandler (0.01s)
--- PASS: TestMiddleware (0.02s)
--- FAIL: TestRouter (0.03s)
--- SKIP: TestContext (0.00s)
--- PASS: TestEngine (0.04s)
--- FAIL: TestBinding (0.05s)
"""
    result = profile.log_parser(log)
    assert len(result) == 6
    assert result["TestHandler"] == "PASSED"
    assert result["TestMiddleware"] == "PASSED"
    assert result["TestRouter"] == "FAILED"
    assert result["TestContext"] == "SKIPPED"
    assert result["TestEngine"] == "PASSED"
    assert result["TestBinding"] == "FAILED"


def test_go_profile_build_image_uses_singularity_builder():
    profile = make_dummy_go_profile()
    with patch.object(profile, "_build_sif") as build_sif:
        profile.build_image()

    build_sif.assert_called_once()
    assert "FROM golang:1.20" in build_sif.call_args.args[0]


def test_go_profile_go_test_command():
    profile = make_dummy_go_profile()
    assert profile.test_cmd == "go test -v ./..."
    assert "go test" in profile.test_cmd
    assert "-v" in profile.test_cmd
    assert "./..." in profile.test_cmd


def test_gin_profile_recipe_content():
    # This test is Gin-specific and checks the Gin profile's recipe
    profile = Gin3c12d2a8()
    recipe = profile.recipe
    assert "FROM golang:1.24" in recipe
    assert f"git clone https://github.com/{profile.mirror_name}" in recipe
    assert "WORKDIR /testbed" in recipe
    assert "go mod tidy" in recipe
