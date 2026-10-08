"""Deterministic stubbing for reconstruction tasks.

Anchors are public top-level functions or public methods of top-level classes.
Stubbing replaces each anchor's def..end span with the signature, docstring,
and placeholder body produced by ``PythonEntity.stub``. Decorators are outside
that span, so they stay in the file. The stub patch is what the solver starts
from; the reference patch is its inverse and restores the original code.
"""

from __future__ import annotations

import ast
import difflib
import os
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from swesmith.bug_gen.adapters.python import PythonEntity, get_entities_from_file_py

MIN_LINES = 4
MIN_COMPLEXITY = 2

_TAB_INDENT = re.compile(r"^[ \t]*\t", re.MULTILINE)


@dataclass
class Anchor:
    entity_id: str
    rel_path: str
    qualname: str
    entity: PythonEntity


@dataclass(frozen=True)
class StubBundle:
    stub_patch: str
    reference_patch: str
    originals: dict[str, str]


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _qualified_functions(tree: ast.Module) -> dict[tuple[int, str], str]:
    """Map (lineno, name) to qualname for module-level functions and direct methods."""
    result: dict[tuple[int, str], str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            result[(node.lineno, node.name)] = node.name
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    result[(child.lineno, child.name)] = f"{node.name}.{child.name}"
    return result


def python_anchors_in_file(
    path: Path,
    rel_path: str,
    min_lines: int = MIN_LINES,
    min_complexity: int = MIN_COMPLEXITY,
) -> list[Anchor]:
    """Return stubbable public functions in one file. Tab-indented files are skipped."""
    text = _read(path)
    if _TAB_INDENT.search(text):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    qualnames = _qualified_functions(tree)
    line_count = len(text.splitlines())
    ends_without_newline = bool(text) and not text.endswith("\n")

    entities: list[PythonEntity] = []
    get_entities_from_file_py(entities, str(path))

    anchors: list[Anchor] = []
    for entity in entities:
        node = entity.node
        if not isinstance(node, ast.FunctionDef):
            continue
        qualname = qualnames.get((node.lineno, node.name))
        if qualname is None:
            continue
        if any(part.startswith("_") for part in qualname.split(".")):
            continue
        if entity.line_end - entity.line_start + 1 < min_lines:
            continue
        if entity.complexity < min_complexity:
            continue
        if ends_without_newline and entity.line_end == line_count:
            continue
        anchors.append(
            Anchor(
                entity_id=f"{rel_path}::{qualname}",
                rel_path=rel_path,
                qualname=qualname,
                entity=entity,
            )
        )
    return anchors


def enumerate_anchors(
    repo_dir: Path,
    is_test_path: Callable[[str, str], bool],
    min_lines: int = MIN_LINES,
    min_complexity: int = MIN_COMPLEXITY,
) -> list[Anchor]:
    """Collect anchors from every non-test Python file under ``repo_dir``."""
    anchors: list[Anchor] = []
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            if not name.endswith(".py") or is_test_path(root, name):
                continue
            path = Path(root) / name
            rel_path = path.relative_to(repo_dir).as_posix()
            anchors.extend(
                python_anchors_in_file(path, rel_path, min_lines, min_complexity)
            )
    return anchors


def stub_file(original: str, anchors: Sequence[Anchor]) -> str:
    """Replace each anchor's def..end span with its stub, bottom-up so line numbers hold."""
    lines = original.splitlines(keepends=True)
    for anchor in sorted(anchors, key=lambda a: a.entity.line_start, reverse=True):
        entity = anchor.entity
        indent = _leading_ws(lines[entity.line_start - 1])
        body = [
            f"{indent}{line}\n" if line.strip() else "\n"
            for line in entity.stub.splitlines()
        ]
        lines[entity.line_start - 1 : entity.line_end] = body
    stubbed = "".join(lines)
    ast.parse(stubbed)
    return stubbed


def build_patch(rel_path: str, before: str, after: str) -> str:
    if before == after:
        return ""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )


def build_stub_bundle(repo_dir: Path, anchors: Sequence[Anchor]) -> StubBundle:
    """Build the solver-facing stub patch and its inverse for the selected anchors."""
    if not anchors:
        raise ValueError("no anchors selected")
    by_file: dict[str, list[Anchor]] = defaultdict(list)
    for anchor in anchors:
        by_file[anchor.rel_path].append(anchor)

    stub_parts: list[str] = []
    reference_parts: list[str] = []
    originals: dict[str, str] = {}
    for rel_path in sorted(by_file):
        original = _read(repo_dir / rel_path)
        stubbed = stub_file(original, by_file[rel_path])
        if stubbed == original:
            raise ValueError(f"stubbing left {rel_path} unchanged")
        originals[rel_path] = original
        stub_parts.append(build_patch(rel_path, original, stubbed))
        reference_parts.append(build_patch(rel_path, stubbed, original))
    return StubBundle(
        stub_patch="".join(stub_parts),
        reference_patch="".join(reference_parts),
        originals=originals,
    )
