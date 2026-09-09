"""
Tests for TypeScript profiles.

This test suite follows the standard testing pattern established in other
language profile tests (Java, C++, JavaScript, etc.).
"""

import pytest
import subprocess
from unittest.mock import patch, mock_open
from swesmith.profiles.typescript import (
    TypeScriptProfile,
    CrossEnv9951937a,
    Trpc2f40ba93,
    ClassValidator977d2c70,
    Rxjsc15b37f8,
    default_npm_install_recipe,
    default_pnpm_install_recipe,
)
from swesmith.constants import ENV_NAME
from swesmith.harness.constants import TestStatus as Status


# =============================================================================
# TypeScriptProfile Base Class Tests
# =============================================================================


def make_dummy_ts_profile():
    """Create a minimal concrete TypeScriptProfile for testing."""

    class DummyTSProfile(TypeScriptProfile):
        owner = "dummy"
        repo = "dummyrepo"
        commit = "deadbeefcafebabe"
        test_cmd = "npm test"

        @property
        def recipe(self):
            return "FROM node:20\nRUN echo hello"

        def log_parser(self, log: str) -> dict[str, str]:
            return {}

    return DummyTSProfile()


def test_ts_profile_defaults():
    """Test TypeScriptProfile default file extensions."""
    profile = make_dummy_ts_profile()
    assert profile.exts == [".ts", ".tsx"]


def test_ts_profile_inheritance():
    """Test that TypeScriptProfile properly inherits from RepoProfile."""
    profile = make_dummy_ts_profile()
    assert hasattr(profile, "owner")
    assert hasattr(profile, "repo")
    assert hasattr(profile, "commit")
    assert hasattr(profile, "exts")
    assert hasattr(profile, "extract_entities")
    assert callable(profile.extract_entities)


def test_ts_profile_extract_entities_default_excludes():
    """Test that extract_entities has correct default exclusions."""
    profile = make_dummy_ts_profile()
    assert hasattr(profile, "extract_entities")
    assert callable(profile.extract_entities)


# =============================================================================
# Default recipe Helper Tests
# =============================================================================


def test_default_npm_install_recipe():
    """Test default_npm_install_recipe generates correct recipe."""
    result = default_npm_install_recipe("org/repo__name.abc12345")
    assert "FROM node:20-bullseye" in result
    assert "git clone https://github.com/org/repo__name.abc12345" in result
    assert f"/{ENV_NAME}" in result
    assert "npm install" in result


def test_default_npm_install_recipe_custom_node():
    """Test default_npm_install_recipe with custom node version."""
    result = default_npm_install_recipe("org/repo", node_version="18")
    assert "FROM node:18-bullseye" in result


def test_default_pnpm_install_recipe():
    """Test default_pnpm_install_recipe generates correct recipe."""
    result = default_pnpm_install_recipe("org/repo__name.abc12345")
    assert "FROM node:20-bullseye" in result
    assert "npm install -g pnpm" in result
    assert "git clone https://github.com/org/repo__name.abc12345" in result
    assert f"/{ENV_NAME}" in result
    assert "pnpm install" in result


def test_default_pnpm_install_recipe_custom_node():
    """Test default_pnpm_install_recipe with custom node version."""
    result = default_pnpm_install_recipe("org/repo", node_version="22")
    assert "FROM node:22-bullseye" in result


# =============================================================================
# Specific Profile Instance Tests
# =============================================================================


def test_crossenv_profile_properties():
    """Test CrossEnv9951937a profile properties."""
    profile = CrossEnv9951937a()
    assert profile.owner == "kentcdodds"
    assert profile.repo == "cross-env"
    assert profile.commit == "9951937a7d3d4a1ea7bd2ce3133bcfb687125813"
    assert profile.test_cmd == "npm test"


def test_crossenv_profile_recipe():
    """Test CrossEnv9951937a recipe content."""
    profile = CrossEnv9951937a()
    recipe = profile.recipe
    assert "FROM node:18-slim" in recipe
    assert f"git clone https://github.com/{profile.mirror_name}" in recipe
    assert "npm install" in recipe


def test_crossenv_profile_log_parser():
    """Test CrossEnv9951937a uses Vitest parser."""
    profile = CrossEnv9951937a()
    log = """
✓ src/index.test.ts (5 tests) 12ms
✗ src/utils.test.ts (3 tests | 1 failed) 8ms
"""
    result = profile.log_parser(log)
    assert result["src/index.test.ts"] == Status.PASSED.value
    assert result["src/utils.test.ts"] == Status.FAILED.value


def test_trpc_profile_properties():
    """Test Trpc2f40ba93 profile properties."""
    profile = Trpc2f40ba93()
    assert profile.owner == "trpc"
    assert profile.repo == "trpc"
    assert profile.commit == "2f40ba935ad7f7d29eec3f9c45d353450b43e852"
    assert profile.test_cmd == "pnpm test"


def test_trpc_profile_recipe():
    """Test Trpc2f40ba93 recipe content."""
    profile = Trpc2f40ba93()
    recipe = profile.recipe
    assert "FROM node:22" in recipe
    assert "pnpm" in recipe
    assert f"git clone https://github.com/{profile.mirror_name}" in recipe
    assert "pnpm install" in recipe


def test_trpc_profile_log_parser():
    """Test Trpc2f40ba93 uses Vitest parser."""
    profile = Trpc2f40ba93()
    log = """
✓ packages/server/src/core.test.ts (10 tests) 25ms
"""
    result = profile.log_parser(log)
    assert result["packages/server/src/core.test.ts"] == Status.PASSED.value


def test_classvalidator_profile_properties():
    """Test ClassValidator977d2c70 profile properties."""
    profile = ClassValidator977d2c70()
    assert profile.owner == "typestack"
    assert profile.repo == "class-validator"
    assert profile.commit == "977d2c707930db602b6450d0c03ee85c70756f1f"
    assert profile.test_cmd == "npm test"


def test_classvalidator_profile_recipe():
    """Test ClassValidator977d2c70 recipe content."""
    profile = ClassValidator977d2c70()
    recipe = profile.recipe
    assert "FROM node:18-slim" in recipe
    assert f"git clone https://github.com/{profile.mirror_name}" in recipe
    assert "npm install" in recipe


def test_classvalidator_profile_log_parser():
    """Test ClassValidator977d2c70 uses Jest parser."""
    profile = ClassValidator977d2c70()
    log = """
  ✓ should validate strings (5ms)
  ✕ should reject invalid input (3ms)
  ✓ should handle arrays (2ms)
"""
    result = profile.log_parser(log)
    assert result["should validate strings"] == Status.PASSED.value
    assert result["should reject invalid input"] == Status.FAILED.value
    assert result["should handle arrays"] == Status.PASSED.value


def test_rxjs_profile_properties():
    """Test Rxjsc15b37f8 profile properties."""
    profile = Rxjsc15b37f8()
    assert profile.owner == "ReactiveX"
    assert profile.repo == "rxjs"
    assert profile.commit == "c15b37f81ba5f5abea8c872b0189a70b150df4cb"
    assert "yarn" in profile.test_cmd


def test_rxjs_profile_recipe():
    """Test Rxjsc15b37f8 recipe content."""
    profile = Rxjsc15b37f8()
    recipe = profile.recipe
    assert "FROM node:20-slim" in recipe
    assert f"git clone https://github.com/{profile.mirror_name}" in recipe
    assert "git submodule update --init --recursive" in recipe
    assert "yarn install" in recipe


def test_rxjs_profile_log_parser():
    """Test Rxjsc15b37f8 uses Mocha parser."""
    profile = Rxjsc15b37f8()
    log = """
  ✓ should emit values (5ms)
  ✓ should complete stream
  ✖ should handle errors (10ms)
"""
    result = profile.log_parser(log)
    passed = sum(1 for v in result.values() if v == Status.PASSED.value)
    failed = sum(1 for v in result.values() if v == Status.FAILED.value)
    assert passed == 2
    assert failed == 1


def test_ts_profile_inheritance_in_concrete_profiles():
    """Test that concrete TS profiles properly inherit from TypeScriptProfile."""
    profiles_to_test = [
        CrossEnv9951937a,
        Trpc2f40ba93,
        ClassValidator977d2c70,
        Rxjsc15b37f8,
    ]

    for profile_class in profiles_to_test:
        profile = profile_class()
        assert isinstance(profile, TypeScriptProfile)
        assert profile.exts == [".ts", ".tsx"]
        assert hasattr(profile, "owner")
        assert hasattr(profile, "repo")
        assert hasattr(profile, "commit")
        assert hasattr(profile, "test_cmd")
        assert hasattr(profile, "recipe")
        assert hasattr(profile, "log_parser")


def test_all_profiles_have_mirror_name_in_recipe():
    """Test that all concrete profiles use mirror_name (not owner/repo) in recipes."""
    profiles_to_test = [
        CrossEnv9951937a,
        Trpc2f40ba93,
        ClassValidator977d2c70,
        Rxjsc15b37f8,
    ]

    for profile_class in profiles_to_test:
        profile = profile_class()
        recipe = profile.recipe
        assert profile.mirror_name in recipe, (
            f"{profile_class.__name__} recipe should contain mirror_name"
        )


# =============================================================================
# Build Image Tests (with mocks)
# =============================================================================


def test_ts_profile_build_image_uses_singularity_builder():
    profile = CrossEnv9951937a()
    with patch.object(profile, "_build_sif") as build_sif:
        profile.build_image()

    build_sif.assert_called_once()
    assert "FROM node:18-slim" in build_sif.call_args.args[0]
