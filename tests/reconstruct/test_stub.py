import shutil
import subprocess
from pathlib import Path

import pytest

from swesmith.reconstruct.stub import (
    build_stub_bundle,
    enumerate_anchors,
    python_anchors_in_file,
)

MODULE = '''import os


def compute_total(items, scale):
    """Sum scaled positive items."""
    total = 0
    for item in items:
        if item > 0:
            total += item * scale
    return total


class Calculator:
    @staticmethod
    def clamp(value, low, high):
        if value < low:
            return low
        if value > high:
            return high
        return value

    def _helper(self, x):
        if x:
            return 1
        return 2
'''

TEST_MODULE = "def test_something():\n    assert True\n"


def _is_test(root: str, name: str) -> bool:
    return name.startswith("test_")


def _write_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "pkg" / "calc.py").write_text(MODULE, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST_MODULE, encoding="utf-8")
    return root


def test_anchors_are_public_functions_and_methods_only(tmp_path):
    repo = _write_repo(tmp_path / "repo")
    anchors = enumerate_anchors(repo, _is_test)
    ids = sorted(a.entity_id for a in anchors)
    assert ids == ["pkg/calc.py::Calculator.clamp", "pkg/calc.py::compute_total"]


def test_short_and_simple_functions_are_skipped(tmp_path):
    path = tmp_path / "small.py"
    path.write_text(
        "def tiny(x):\n    return x\n\n"
        "def linear(a, b):\n    c = a + b\n    d = c * 2\n    return d\n",
        encoding="utf-8",
    )
    assert python_anchors_in_file(path, "small.py") == []


def test_tab_indented_files_are_skipped(tmp_path):
    path = tmp_path / "tabs.py"
    path.write_text(
        "def f(x):\n\tif x:\n\t\ty = 1\n\telse:\n\t\ty = 2\n\treturn y\n",
        encoding="utf-8",
    )
    assert python_anchors_in_file(path, "tabs.py") == []


def test_stub_keeps_decorator_and_signature_and_drops_body(tmp_path):
    repo = _write_repo(tmp_path / "repo")
    anchors = [
        a
        for a in enumerate_anchors(repo, _is_test)
        if a.qualname == "Calculator.clamp"
    ]
    bundle = build_stub_bundle(repo, anchors)
    stubbed = _apply_to_copy(repo, bundle.stub_patch, tmp_path / "stubbed")
    assert "    @staticmethod\n    def clamp(value, low, high):" in stubbed
    assert "if value < low" not in stubbed
    assert "compute_total" in stubbed
    assert "    pass\n" in stubbed


def _git(cwd: Path, *args: str, stdin: str | None = None) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        input=stdin,
        text=True,
        check=True,
        capture_output=True,
    )


def _apply_to_copy(repo: Path, patch: str, dest: Path) -> str:
    """Copy the repo to ``dest``, apply ``patch`` forward, and return the stubbed module."""
    shutil.copytree(repo, dest, ignore=shutil.ignore_patterns(".git"))
    patch_path = dest / "_change.diff"
    patch_path.write_text(patch, encoding="utf-8")
    _git(dest, "init", "-q")
    _git(dest, "apply", "_change.diff")
    patch_path.unlink()
    return (dest / "pkg" / "calc.py").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_stub_and_reference_patches_round_trip_through_git_apply(tmp_path):
    repo = _write_repo(tmp_path / "repo")
    anchors = enumerate_anchors(repo, _is_test)
    bundle = build_stub_bundle(repo, anchors)
    original = (repo / "pkg" / "calc.py").read_text(encoding="utf-8")

    stubbed = _apply_to_copy(repo, bundle.stub_patch, tmp_path / "stubbed")
    assert stubbed != original

    restored_dir = tmp_path / "restored"
    shutil.copytree(repo, restored_dir, ignore=shutil.ignore_patterns(".git"))
    (restored_dir / "pkg" / "calc.py").write_text(stubbed, encoding="utf-8")
    (restored_dir / "_ref.diff").write_text(bundle.reference_patch, encoding="utf-8")
    _git(restored_dir, "init", "-q")
    _git(restored_dir, "apply", "_ref.diff")
    assert (restored_dir / "pkg" / "calc.py").read_text(encoding="utf-8") == original


def test_bundle_originals_match_disk(tmp_path):
    repo = _write_repo(tmp_path / "repo")
    anchors = enumerate_anchors(repo, _is_test)
    bundle = build_stub_bundle(repo, anchors)
    assert bundle.originals == {"pkg/calc.py": MODULE}


def test_empty_selection_is_rejected(tmp_path):
    repo = _write_repo(tmp_path / "repo")
    with pytest.raises(ValueError):
        build_stub_bundle(repo, [])
