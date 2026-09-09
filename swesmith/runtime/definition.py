"""Translate the repository profile recipes into Apptainer definitions."""

from __future__ import annotations

import json
import re
import shlex

from pathlib import Path


class RecipeTranslationError(ValueError):
    """Raised when a profile recipe cannot be translated safely."""


def _logical_lines(content: str) -> list[tuple[int, str]]:
    lines = content.splitlines()
    result: list[tuple[int, str]] = []
    current = ""
    start = 0
    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not current:
            start = number
        if stripped.endswith("\\"):
            current += stripped[:-1].rstrip() + " "
        else:
            current += stripped
            if current:
                result.append((start, current))
            current = ""
    if current:
        result.append((start, current))
    return result


def _env_assignment(value: str, line: int) -> tuple[str, str]:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", value)
    if match:
        return match.group(1), match.group(2)
    pieces = value.split(None, 1)
    if len(pieces) == 2 and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", pieces[0]):
        return pieces[0], pieces[1]
    raise RecipeTranslationError(f"Invalid ENV syntax on line {line}: {value}")


def _env_assignments(value: str, line: int) -> list[tuple[str, str]]:
    """Parse Docker's ``ENV key=value ...`` form without losing expansions."""
    assignments = list(
        re.finditer(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=", value)
    )
    if assignments and assignments[0].start() == 0:
        result = []
        for index, match in enumerate(assignments):
            end = (
                assignments[index + 1].start()
                if index + 1 < len(assignments)
                else len(value)
            )
            result.append((match.group(1), value[match.end() : end].strip()))
        return result
    return [_env_assignment(value, line)]


def translate_recipe(content: str, *, context_dir: Path | None = None) -> str:
    """Translate the profile recipe language into an Apptainer definition."""
    bootstrap: str | None = None
    post: list[str] = []
    environment: list[str] = []
    labels: list[str] = []
    files: list[str] = []
    runscript: list[str] = []
    workdir = "/"

    for line, instruction in _logical_lines(content):
        if instruction.startswith("#"):
            continue
        match = re.match(r"^(\w+)\s*(.*)$", instruction)
        if not match:
            raise RecipeTranslationError(f"Cannot parse line {line}: {instruction}")
        op, value = match.group(1).upper(), match.group(2).strip()

        if op == "FROM":
            if bootstrap is not None:
                raise RecipeTranslationError(f"Multiple FROM instructions; line {line}")
            parts = shlex.split(value)
            parts = [part for part in parts if not part.startswith("--")]
            if not parts:
                raise RecipeTranslationError(f"Invalid FROM syntax on line {line}")
            bootstrap = parts[0]
            if len(parts) > 1 and parts[1].upper() != "AS":
                raise RecipeTranslationError(f"FROM flags are unsupported on line {line}: {value}")
        elif op == "RUN":
            value = re.sub(r"--mount=type=ssh(?:,required=(?:true|false))?\s*", "", value)
            post.append(f"cd {workdir} && {value}")
        elif op == "ENV":
            for key, env_value in _env_assignments(value, line):
                post.append(f"export {key}={env_value}")
                environment.append(f"export {key}={env_value}")
        elif op == "ARG":
            if "=" in value:
                key, default = value.split("=", 1)
                post.append(f"export {key.strip()}={default}")
            elif value:
                post.append(f"export {value}")
            else:
                raise RecipeTranslationError(f"Invalid ARG syntax on line {line}")
        elif op == "WORKDIR":
            workdir = value
            post.append(f"mkdir -p {workdir}")
        elif op == "COPY":
            pieces = value.split()
            if len(pieces) != 2 or pieces[0].startswith("--"):
                raise RecipeTranslationError(f"COPY form is unsupported on line {line}: {value}")
            source, destination = pieces
            if context_dir is not None and not (context_dir / source).exists() and not Path(source).is_absolute():
                raise RecipeTranslationError(f"COPY source does not exist on line {line}: {source}")
            files.append(f"{source} {destination}")
        elif op == "LABEL":
            key, label_value = _env_assignment(value, line)
            labels.append(f"{key} {label_value}")
        elif op in {"CMD", "ENTRYPOINT"}:
            command = value
            if value.startswith("["):
                try:
                    command = " ".join(json.loads(value))
                except (json.JSONDecodeError, TypeError):
                    raise RecipeTranslationError(f"Invalid {op} JSON on line {line}: {value}")
            runscript = [f"cd {workdir}", f"exec {command} \"$@\""]
        elif op == "USER" and value in {"root", "0"}:
            continue
        elif op in {"SHELL", "EXPOSE", "VOLUME", "HEALTHCHECK", "ONBUILD", "ADD"}:
            raise RecipeTranslationError(f"{op} is unsupported on line {line}")
        else:
            raise RecipeTranslationError(f"{op} is unsupported on line {line}")

    if bootstrap is None:
        raise RecipeTranslationError("Profile recipe does not contain a FROM instruction")
    sections = [f"Bootstrap: docker\nFrom: {bootstrap}", "%post"]
    sections.append("\n".join(f"    {command}" for command in post) or "    true")
    if files:
        sections.extend(["%files", "\n".join(f"    {entry}" for entry in files)])
    if environment:
        sections.extend(["%environment", "\n".join(f"    {entry}" for entry in environment)])
    if labels:
        sections.extend(["%labels", "\n".join(f"    {entry}" for entry in labels)])
    if runscript:
        sections.extend(["%runscript", "\n".join(f"    {entry}" for entry in runscript)])
    return "\n\n".join(sections) + "\n"
