"""Build source-only reconstruction tasks for one Python repository.

Per candidate anchor (shuffled with a fixed seed):

1. design: an agent picks up to 3 same-file public functions, writes a
   solver-visible instruction and REQ-n requirements, or rejects.
2. stub: the selected bodies are replaced with ``PythonEntity.stub``.
3. tests: an agent writes one hidden pytest file that cites REQ-n ids. It must
   pass on the original code, up to ``max_test_rounds`` attempts.
4. review: one audit call returns sound, revise, or reject.
5. controls: start runs (stub, must fail) and reference runs (original, must
   pass) decide FAIL_TO_PASS. Any inconsistency rejects the candidate.

Accepted tasks are written as JSON records that the existing eval harness can
grade: the stub patch is the solver start, the reference patch restores it, and
the verifier directory is mounted read-only at ``/hidden_tests``.
"""

from __future__ import annotations

import json
import random
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from swesmith.constants import (
    DEFAULT_BASE_COMMIT,
    KEY_BASE_COMMIT,
    KEY_IMAGE_NAME,
    KEY_INSTANCE_ID,
    KEY_PATCH,
    KEY_REFERENCE_PATCH,
    KEY_VERIFIER_DIR,
    LOG_DIR_TASKS,
    LOG_ROOT,
    UTF8,
    generate_hash,
)
from swesmith.harness.constants import FAIL_TO_PASS, LOG_TEST_OUTPUT, PASS_TO_PASS
from swesmith.harness.grading import read_test_output
from swesmith.harness.utils import run_patch_in_container
from swesmith.llm import complete
from swesmith.profiles import registry
from swesmith.profiles.python import PythonProfile
from swesmith.reconstruct.agent import (
    AgentError,
    ChatFn,
    Message,
    parse_json_object,
    run_agent,
)
from swesmith.reconstruct.stub import (
    Anchor,
    StubBundle,
    build_stub_bundle,
    enumerate_anchors,
)
from swesmith.reconstruct.verifier import (
    check_requirement_mapping,
    derive_fail_to_pass,
    reference_gate_problems,
    requirement_ids,
    validate_requirements,
)
from swesmith.runtime.singularity import SingularityRunner, make_workspace

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "reconstruct"
RECONSTRUCTION_ROOT = LOG_ROOT / "reconstruct"
TEST_FILE_NAME = "test_reconstruction.py"
MAX_SELECTED = 3


@dataclass(frozen=True)
class ReconstructConfig:
    target: int = 5
    max_candidates: int = 50
    max_design_turns: int = 12
    max_test_turns: int = 12
    max_test_rounds: int = 3
    start_runs: int = 2
    reference_runs: int = 4
    seed: int = 24
    exec_timeout: int = 120


class Rejected(Exception):
    """A candidate failed a gate. The message is recorded as the rejection reason."""


class ControlError(RuntimeError):
    """A control run did not produce a usable test result."""


def default_chat(messages: list[Message]) -> str:
    response = complete(messages=messages)
    return response.choices[0].message.content


def load_prompt(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / f"{name}.yml").read_text(encoding=UTF8))


def _system_user(prompt: dict, **values: str) -> list[Message]:
    return [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["instance"].format(**values)},
    ]


def _exec(runner: SingularityRunner, command: str, timeout: int) -> str:
    """Run one agent command. The inner bash keeps ``&&`` chains intact: the runner
    prefixes commands with ``exec``, which would otherwise drop everything after the
    first command in a chain."""
    result = runner.exec_run(f"bash -c {shlex.quote(command)}", timeout=timeout)
    text = result.output.decode(UTF8, errors="replace")
    if result.timed_out:
        text += f"\n[timed out after {timeout}s]"
    return f"{text}\n[exit {result.exit_code}]"


def _reset_checkout(runner: SingularityRunner) -> None:
    """Undo any edits an agent made so the stub is built from the original tree."""
    runner.exec_run("git checkout -- .")
    runner.exec_run("git clean -fdq")


def _format_requirements(requirements: list[dict]) -> str:
    return "\n".join(f"- {item['id']}: {item['text'].strip()}" for item in requirements)


def _format_sources(anchors: list[Anchor]) -> str:
    return "\n\n".join(
        f"# {anchor.entity_id}\n{anchor.entity.src_code}" for anchor in anchors
    )


def render_problem_statement(instruction: str, requirements: list[dict]) -> str:
    lines = [instruction.strip(), "", "Acceptance criteria:"]
    lines += [f"- {item['id']}: {item['text'].strip()}" for item in requirements]
    return "\n".join(lines) + "\n"


def _design(
    anchor: Anchor,
    siblings: list[Anchor],
    runner: SingularityRunner,
    rp,
    config: ReconstructConfig,
    chat: ChatFn,
) -> tuple[list[str], str, list[dict]]:
    prompt = load_prompt("design")
    messages = _system_user(
        prompt,
        repo_name=rp.mirror_name,
        anchor_id=anchor.entity_id,
        candidate_list="\n".join(f"- {s.entity_id}" for s in siblings),
        anchor_source=anchor.entity.src_code,
    )
    action = run_agent(
        chat,
        messages,
        lambda command: _exec(runner, command, config.exec_timeout),
        terminal={"submit", "reject"},
        max_turns=config.max_design_turns,
    )
    _reset_checkout(runner)
    if action["action"] == "reject":
        raise Rejected(f"design rejected: {action.get('reason', 'no reason given')}")

    selected = action.get("selected_ids")
    sibling_ids = {s.entity_id for s in siblings}
    if not isinstance(selected, list) or not all(isinstance(x, str) for x in selected):
        raise Rejected("design selected_ids must be a list of strings")
    if anchor.entity_id not in selected:
        raise Rejected("design did not select the anchor entity")
    if not set(selected) <= sibling_ids:
        raise Rejected("design selected entities outside the candidate list")
    if not 1 <= len(selected) <= MAX_SELECTED:
        raise Rejected(f"design selected {len(selected)} entities; allowed 1-{MAX_SELECTED}")

    instruction = action.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise Rejected("design instruction is empty")

    requirements = action.get("requirements")
    problems = validate_requirements(requirements)
    if problems:
        raise Rejected("bad requirements: " + "; ".join(problems))
    return list(dict.fromkeys(selected)), instruction, requirements


def _control_status(
    instance: dict,
    patch: str | None,
    run_id: str,
    log_dir: Path,
    rp,
) -> dict[str, str]:
    outcome = run_patch_in_container(
        instance,
        run_id=run_id,
        log_dir=log_dir,
        timeout=rp.timeout_ref,
        patch=patch,
    )
    if outcome is None:
        raise ControlError(f"{run_id}: run did not start")
    output_path = log_dir / run_id / instance[KEY_INSTANCE_ID] / LOG_TEST_OUTPUT
    content, found = read_test_output(str(output_path))
    if content is None:
        raise ControlError(f"{run_id}: tests did not run (patch failed or timed out)")
    if not found:
        raise ControlError(f"{run_id}: no end marker in test output")
    return rp.log_parser(content)


def _write_tests(verifier_dir: Path, content: str) -> None:
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (verifier_dir / TEST_FILE_NAME).write_text(content, encoding=UTF8)


def _gate_tests(
    content: str,
    requirement_list: list[str],
    instance: dict,
    bundle: StubBundle,
    log_dir: Path,
    rp,
    run_id: str,
) -> list[str]:
    """Static mapping check, then the reference gate. Returns problems."""
    problems = check_requirement_mapping(content, requirement_list)
    if problems:
        return problems
    _write_tests(Path(instance[KEY_VERIFIER_DIR]), content)
    reference_map = _control_status(
        instance, bundle.reference_patch, run_id, log_dir, rp
    )
    return reference_gate_problems(reference_map)


def _author_tests(
    selected: list[Anchor],
    instruction: str,
    requirements: list[dict],
    instance: dict,
    bundle: StubBundle,
    runner: SingularityRunner,
    log_dir: Path,
    rp,
    config: ReconstructConfig,
    chat: ChatFn,
    candidate_tag: str,
) -> str:
    prompt = load_prompt("tests")
    messages = _system_user(
        prompt,
        repo_name=rp.mirror_name,
        instruction=instruction,
        requirements=_format_requirements(requirements),
        sources=_format_sources(selected),
    )
    requirement_list = requirement_ids(requirements)
    for round_index in range(config.max_test_rounds):
        action = run_agent(
            chat,
            messages,
            lambda command: _exec(runner, command, config.exec_timeout),
            terminal={"submit_tests"},
            max_turns=config.max_test_turns,
        )
        _reset_checkout(runner)
        content = action.get("content")
        if not isinstance(content, str) or not content.strip():
            problems = ['submit_tests needs a non-empty "content" string']
        else:
            problems = _gate_tests(
                content,
                requirement_list,
                instance,
                bundle,
                log_dir,
                rp,
                run_id=f"{candidate_tag}-gate-{round_index}",
            )
        if not problems:
            return content
        messages.append(
            {
                "role": "user",
                "content": "Rejected: "
                + "; ".join(problems[:10])
                + ". Fix the tests and resubmit with submit_tests.",
            }
        )
    raise Rejected(
        f"tests did not pass the reference gate after {config.max_test_rounds} rounds"
    )


def _review(
    selected: list[Anchor],
    instruction: str,
    requirements: list[dict],
    tests: str,
    rp,
    chat: ChatFn,
) -> tuple[str, str, str | None]:
    """Return (verdict, reason, revised_tests) from a single audit call."""
    prompt = load_prompt("review")
    messages = _system_user(
        prompt,
        repo_name=rp.mirror_name,
        instruction=instruction,
        requirements=_format_requirements(requirements),
        sources=_format_sources(selected),
        tests=tests,
    )
    data = parse_json_object(chat(messages))
    if data is None:
        raise Rejected("review reply was not a JSON object")
    verdict = data.get("verdict")
    reason = str(data.get("reason", ""))
    if verdict == "sound":
        return verdict, reason, None
    if verdict == "revise":
        revised = data.get("revised_tests")
        if not isinstance(revised, str) or not revised.strip():
            raise Rejected("review asked to revise but gave no revised_tests")
        return verdict, reason, revised
    raise Rejected(f"review rejected: {reason}")


def _run_controls(
    instance: dict,
    tests_log_dir: Path,
    candidate_tag: str,
    rp,
    config: ReconstructConfig,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    start_maps = [
        _control_status(instance, None, f"{candidate_tag}-start-{i}", tests_log_dir, rp)
        for i in range(config.start_runs)
    ]
    reference_maps = [
        _control_status(
            instance,
            instance[KEY_REFERENCE_PATCH],
            f"{candidate_tag}-ref-{i}",
            tests_log_dir,
            rp,
        )
        for i in range(config.reference_runs)
    ]
    return start_maps, reference_maps


def _process_candidate(
    anchor: Anchor,
    siblings: list[Anchor],
    by_id: dict[str, Anchor],
    repo_dir: Path,
    rp,
    config: ReconstructConfig,
    chat: ChatFn,
    output_root: Path,
) -> dict:
    runner = SingularityRunner(rp.image_path, repo_dir)
    # A previous candidate may have died mid-agent; the stub must come from a clean tree.
    _reset_checkout(runner)
    selected_ids, instruction, requirements = _design(
        anchor, siblings, runner, rp, config, chat
    )
    selected = [by_id[entity_id] for entity_id in selected_ids]
    bundle = build_stub_bundle(repo_dir, selected)

    instance_id = f"{rp.mirror_name}.recon-{generate_hash(bundle.stub_patch + instruction)}"
    candidate_tag = instance_id.rsplit(".", 1)[1]
    verifier_dir = (output_root / "verifiers" / instance_id).resolve()
    log_dir = output_root / "runs"
    instance = {
        KEY_INSTANCE_ID: instance_id,
        "repo": rp.mirror_name,
        KEY_IMAGE_NAME: rp.image_name,
        KEY_BASE_COMMIT: DEFAULT_BASE_COMMIT,
        KEY_PATCH: bundle.stub_patch,
        KEY_REFERENCE_PATCH: bundle.reference_patch,
        KEY_VERIFIER_DIR: str(verifier_dir),
        FAIL_TO_PASS: [],
        PASS_TO_PASS: [],
    }

    tests = _author_tests(
        selected,
        instruction,
        requirements,
        instance,
        bundle,
        runner,
        log_dir,
        rp,
        config,
        chat,
        candidate_tag,
    )
    verdict, reason, revised = _review(
        selected, instruction, requirements, tests, rp, chat
    )
    if revised is not None:
        problems = _gate_tests(
            revised,
            requirement_ids(requirements),
            instance,
            bundle,
            log_dir,
            rp,
            run_id=f"{candidate_tag}-revise-gate",
        )
        if problems:
            raise Rejected("revised tests failed: " + "; ".join(problems[:5]))
        tests = revised
    _write_tests(verifier_dir, tests)

    start_maps, reference_maps = _run_controls(
        instance, log_dir, candidate_tag, rp, config
    )
    fail_to_pass, problems = derive_fail_to_pass(start_maps, reference_maps)
    if problems:
        raise Rejected("controls: " + "; ".join(problems[:5]))

    instance[FAIL_TO_PASS] = fail_to_pass
    instance["problem_statement"] = render_problem_statement(instruction, requirements)
    instance["requirements"] = requirements
    instance["selected_entities"] = selected_ids
    instance["review"] = {"verdict": verdict, "reason": reason}
    instance["controls"] = {
        "start_runs": len(start_maps),
        "reference_runs": len(reference_maps),
    }
    return instance


def _record_rejection(output_root: Path, entity_id: str, reason: str) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    with open(output_root / "rejections.jsonl", "a", encoding=UTF8) as handle:
        handle.write(json.dumps({"entity": entity_id, "reason": reason}) + "\n")


def task_file_path(repo_name: str) -> Path:
    return LOG_DIR_TASKS / f"{repo_name}__reconstruct.json"


def build_reconstruction_tasks(
    repo_name: str,
    config: ReconstructConfig | None = None,
    chat: ChatFn | None = None,
) -> list[dict]:
    """Generate up to ``config.target`` accepted tasks for one Python profile."""
    config = config or ReconstructConfig()
    chat = chat or default_chat
    rp = registry.get(repo_name)
    if not isinstance(rp, PythonProfile):
        raise ValueError(f"reconstruction supports Python profiles only: {repo_name}")
    rp.require_image()

    output_root = RECONSTRUCTION_ROOT / rp.mirror_name
    output_root.mkdir(parents=True, exist_ok=True)
    workspace_root = make_workspace(prefix=f"reconstruct-{rp.mirror_name}-")
    try:
        repo_dir = workspace_root / "repo"
        rp.clone(dest=str(repo_dir))
        anchors = enumerate_anchors(repo_dir, rp._is_test_path)
        anchors.sort(key=lambda a: a.entity_id)
        random.Random(config.seed).shuffle(anchors)
        by_file: dict[str, list[Anchor]] = {}
        for anchor in anchors:
            by_file.setdefault(anchor.rel_path, []).append(anchor)
        by_id = {anchor.entity_id: anchor for anchor in anchors}

        accepted: list[dict] = []
        for anchor in anchors[: config.max_candidates]:
            if len(accepted) >= config.target:
                break
            siblings = by_file[anchor.rel_path]
            try:
                instance = _process_candidate(
                    anchor,
                    siblings,
                    by_id,
                    repo_dir,
                    rp,
                    config,
                    chat,
                    output_root,
                )
            except (Rejected, AgentError, ControlError, ValueError, SyntaxError, OSError) as exc:
                _record_rejection(output_root, anchor.entity_id, str(exc))
                continue
            accepted.append(instance)
    finally:
        shutil.rmtree(workspace_root, ignore_errors=True)

    task_file = task_file_path(rp.mirror_name)
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps(accepted, indent=2), encoding=UTF8)
    return accepted
