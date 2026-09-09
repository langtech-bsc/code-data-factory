"""Shared task execution helpers for the Singularity-backed harness."""

from __future__ import annotations

import fnmatch
import logging
import shlex
import shutil
import traceback

from concurrent.futures import ThreadPoolExecutor, as_completed
from logging import Logger
from pathlib import Path

from swesmith.harness.constants import (
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS,
    LOG_INSTANCE,
    LOG_TEST_OUTPUT,
    RUN_EVALUATION_LOG_DIR,
    TESTS_TIMEOUT,
)
from swesmith.constants import (
    GIT_APPLY_CMDS,
    KEY_INSTANCE_ID,
    TEST_OUTPUT_END,
    TEST_OUTPUT_START,
    UTF8,
)
from swesmith.profiles import registry
from swesmith.runtime.singularity import SingularityRunner, make_workspace
from unidiff import PatchSet


class EvaluationError(RuntimeError):
    """Raised when a patch cannot be applied to a task workspace."""

    def __init__(self, instance_id: str, message: str, logger: Logger):
        super().__init__(message)
        self.instance_id = instance_id
        self.logger = logger


def setup_logger(name: str, log_file: Path) -> Logger:
    logger = logging.getLogger(f"swesmith.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.log_file = str(log_file)  # type: ignore[attr-defined]
    return logger


def close_logger(logger: Logger) -> None:
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)


def matches_instance_filter(instance_id: str, instance_ids: list[str] | None) -> bool:
    if instance_ids is None:
        return True
    return any(
        instance_id == item or fnmatch.fnmatch(instance_id, item)
        for item in instance_ids
    )


def _decode(output: bytes) -> str:
    return output.decode(UTF8, errors="replace")


def _apply_patch(
    instance_id: str,
    runner: SingularityRunner,
    logger: Logger,
    patch_path: str,
    is_gold: bool = False,
) -> None:
    last_output = ""
    for git_apply_cmd in GIT_APPLY_CMDS:
        command = f"{git_apply_cmd} {shlex.quote(patch_path)}"
        if is_gold:
            command = f"{git_apply_cmd} --reverse {shlex.quote(patch_path)}"
        result = runner.exec_run(command)
        last_output = _decode(result.output)
        if result.exit_code == 0:
            logger.info(f"{APPLY_PATCH_PASS}:\n{last_output}")
            return
        logger.info(
            "Failed to apply patch with %s.\n%s\nTrying again...",
            command,
            last_output,
        )
    message = f"{APPLY_PATCH_FAIL}:\n{last_output}"
    logger.info(message)
    raise EvaluationError(instance_id, message, logger)


def _checkout(runner: SingularityRunner, commit: str, logger: Logger) -> bool:
    result = runner.exec_run(f"git fetch && git checkout {shlex.quote(commit)}")
    if result.exit_code:
        logger.info("CHECKOUT FAILED: %s", _decode(result.output))
        return False
    return True


def run_patch_in_container(
    instance: dict,
    run_id: str,
    log_dir: Path,
    timeout: int,
    patch: str | None = None,
    commit: str | None = None,
    f2p_only: bool = False,
    is_gold: bool = False,
) -> tuple[Logger, bool] | None:
    """Run validation/evaluation in an isolated Singularity workspace.

    The historical function name is retained for downstream callers. No
    container daemon is used.
    """
    instance_id = instance[KEY_INSTANCE_ID]
    rp = registry.get_from_inst(instance)
    is_eval = log_dir == RUN_EVALUATION_LOG_DIR
    output_dir = log_dir / run_id / instance_id
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(f"{run_id}.{instance_id}", output_dir / LOG_INSTANCE)
    workspace_root: Path | None = None
    workspace: Path | None = None
    try:
        rp.require_image()
        workspace_root = make_workspace(prefix=f"swesmith-{instance_id}-")
        workspace = workspace_root / "repo"
        rp.clone(dest=str(workspace))

        ssh_env = {}
        if rp._is_repo_private():
            from swesmith.profiles.base import _find_ssh_key

            key_file = _find_ssh_key()
            if key_file is None:
                raise ValueError(
                    "Repo is private but no SSH key found. Set "
                    "GITHUB_USER_SSH_KEY or add a key to ~/.ssh/."
                )
            ssh_env["GIT_SSH_COMMAND"] = (
                f"ssh -i {key_file} -o StrictHostKeyChecking=accept-new "
                "-o IdentitiesOnly=yes"
            )

        runner = SingularityRunner(rp.image_path, workspace, environment=ssh_env)
        if commit is not None:
            if not _checkout(runner, commit, logger):
                return logger, False
            if is_eval and not _checkout(runner, "HEAD~1", logger):
                logger.info("CHECKOUT TO BUG STAGE FAILED")
                return logger, False

        if patch:
            changed_files = [x.path for x in PatchSet(patch)]
            if changed_files:
                runner.exec_run("git checkout -- " + shlex.join(changed_files))
            patch_file = workspace / ".swesmith_patch.diff"
            patch_file.write_text(patch)
            _apply_patch(
                instance_id,
                runner,
                logger,
                "/testbed/.swesmith_patch.diff",
                is_gold=is_gold,
            )
            patch_file.unlink(missing_ok=True)

            if is_eval:
                f2p_files, p2p_files = rp.get_test_files(instance)
                test_files = f2p_files + p2p_files
                if test_files:
                    runner.exec_run("git checkout -- " + shlex.join(test_files))

        test_command, _ = rp.get_test_cmd(instance, f2p_only=f2p_only)
        eval_script = "\n".join(
            [
                "set -uxo pipefail",
                f": {shlex.quote(TEST_OUTPUT_START)}",
                test_command,
                f": {shlex.quote(TEST_OUTPUT_END)}",
            ]
        )
        result = runner.exec_run(
            f"bash -lc {shlex.quote(eval_script)}", timeout=timeout
        )
        test_output = _decode(result.output)
        output_path = output_dir / LOG_TEST_OUTPUT
        output_path.write_text(test_output)
        if result.timed_out:
            with output_path.open("a") as output_file:
                output_file.write(f"\n\n{TESTS_TIMEOUT}: {timeout} seconds exceeded")
        logger.info("Test Runtime: %.2f seconds", result.runtime)
        logger.info("Test output written to %s", output_path)
        return logger, result.timed_out
    except Exception as exc:
        logger.exception("Error validating %s: %s", instance_id, exc)
        print(f"Error validating {instance_id}: {exc}")
        return logger, False
    finally:
        if workspace_root is not None:
            shutil.rmtree(workspace_root, ignore_errors=True)


def run_threadpool(func, payloads, max_workers):
    if max_workers <= 0:
        return run_sequential(func, payloads)
    succeeded, failed = [], []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(func, *payload): payload for payload in payloads}
        for future in as_completed(futures):
            try:
                future.result()
                succeeded.append(futures[future])
            except Exception:
                traceback.print_exc()
                failed.append(futures[future])
    return succeeded, failed


def run_sequential(func, payloads):
    succeeded, failed = [], []
    for payload in payloads:
        try:
            func(*payload)
            succeeded.append(payload)
        except Exception:
            traceback.print_exc()
            failed.append(payload)
    return succeeded, failed
