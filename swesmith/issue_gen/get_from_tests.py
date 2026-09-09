"""
Purpose: Given a task instance, use the execution output of running tests as issue text.

uv run python swesmith/issue_gen/get_from_tests.py logs/experiments/*.json \
    --config_file configs/issue_gen/ig_tests.yaml \
    --model glm-5.3-flash \
    --n_workers 1
"""

import argparse
import json
import random
import shlex
import shutil
import yaml

from concurrent.futures import ThreadPoolExecutor, as_completed
from datasets import load_dataset
from pathlib import Path
from swesmith.harness.constants import FAIL_TO_PASS
from swesmith.constants import (
    KEY_INSTANCE_ID,
    LOG_DIR_ISSUE_GEN,
    TEST_OUTPUT_END,
    TEST_OUTPUT_START,
)
from swesmith.issue_gen.utils import get_test_function
from swesmith.profiles import RepoProfile, registry
from swesmith.profiles.python import PythonProfile
from swesmith.runtime.singularity import SingularityRunner, make_workspace
from swesmith.llm import complete
from tqdm.auto import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

LOG_FILE_ISSUE = "issue_test.txt"
LOG_FILE_METADATA = "metadata_test.json"
SWEBV_PS = load_dataset("SWE-bench/SWE-bench_Verified", split="test")[
    "problem_statement"
]
TEST_INFO = """**Test Source Code**
```python
{func}
```

**Test Output**
```
{output}
```
"""


def get_verbose_test_cmd(instance: dict, rp: RepoProfile, test_idx: int | None = None):
    """
    Get test command that runs a random F2P test verbosely.
    """
    test_cmd = rp.test_cmd
    # TODO: This should probably be changed, or incorporated into the profile
    if test_cmd == PythonProfile.test_cmd:
        test_cmd = test_cmd.replace(
            PythonProfile.test_cmd, "pytest -v --showlocals --tb=long --color=no"
        )
    f2p_test = (
        random.choice(instance[FAIL_TO_PASS])
        if test_idx is None
        else instance[FAIL_TO_PASS][test_idx]
    )
    test_cmd += " " + f2p_test
    return test_cmd


def run_command_in_container(instance: dict, command: str, rp: RepoProfile):
    """
    Run a command in an isolated Singularity workspace.
    """
    instance_id = instance[KEY_INSTANCE_ID]
    rp.require_image()
    workspace_root = make_workspace(prefix=f"swesmith-issue-{instance_id}-")
    workspace = workspace_root / "repo"
    try:
        rp.clone(dest=str(workspace))
        runner = SingularityRunner(rp.image_path, workspace)
        script = "\n".join(
            [
                "set -uxo pipefail",
                "git fetch",
                f"git checkout {shlex.quote(instance_id)}",
                f": {shlex.quote(TEST_OUTPUT_START)}",
                command,
                f": {shlex.quote(TEST_OUTPUT_END)}",
            ]
        )
        result = runner.exec_run(f"bash -lc {shlex.quote(script)}", timeout=rp.timeout)
        test_output = result.output.decode(errors="replace")
    finally:
        shutil.rmtree(workspace_root, ignore_errors=True)
    start_idx = test_output.find(TEST_OUTPUT_START) + len(TEST_OUTPUT_START)
    end_idx = test_output.find(TEST_OUTPUT_END)
    lines = [
        x
        for x in test_output[start_idx:end_idx].splitlines()
        if x.strip() != command.strip()
    ]
    test_output = "\n".join(lines[1:-1])
    return test_output


def _process_instance(instance: dict, config_file: str | None, model: str | None):
    log_dir = (
        LOG_DIR_ISSUE_GEN
        / "from_tests"
        / instance["repo"].split("/")[-1]
        / instance[KEY_INSTANCE_ID]
    )
    path_metadata = log_dir / LOG_FILE_METADATA
    path_issue = log_dir / LOG_FILE_ISSUE

    cloned = False
    if log_dir.exists() and path_metadata.exists() and path_issue.exists():
        with open(path_metadata, "r") as f:
            metadata = json.load(f)
        test_idx = metadata["test_idx"]
        test_info = metadata["test_info"]
        test_output = metadata["test_output"]
        test_src = metadata["test_src"]

        if (
            metadata["generated"]
            or not metadata["generated"]
            and not config_file
            and not model
        ):
            return {"completed": 1, "timed_out": 0, "failed": 0}
    else:
        test_idx = random.randint(0, len(instance[FAIL_TO_PASS]) - 1)
        rp = registry.get_from_inst(instance)
        cmd = get_verbose_test_cmd(instance, rp, test_idx)
        test_output = run_command_in_container(instance, cmd, rp)
        test_func = get_test_function(instance, test_idx)
        test_src = test_func["test_src"]
        test_info = TEST_INFO.format(func=test_src, output=test_output)
        cloned = test_func["cloned"]

    generated = None
    if config_file and model:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
        messages = [
            {"content": config["system"], "role": "system"},
            {
                "content": config["demonstration"].format(
                    **{"demo": random.choice(SWEBV_PS)}
                ),
                "role": "user",
            },
            {
                "content": config["instance"].format(**{"input": test_info}),
                "role": "user",
            },
        ]
        response = complete(model=model, messages=messages, n=1, temperature=0)
        generated = response.choices[0].message.content

    log_dir.mkdir(parents=True, exist_ok=True)
    with open(path_metadata, "w") as f:
        json.dump(
            {
                "test_idx": test_idx,
                "test_info": test_info,
                "test_output": test_output,
                "test_src": test_src,
                "generated": generated,
                "cost": 0,
            },
            f,
            indent=2,
        )
    with open(path_issue, "w") as f:
        f.write(generated if generated else test_info)
    rv = {"completed": 1, "timed_out": 0, "failed": 0}
    if cloned:
        rv["cleanup"] = instance["repo"].split("/")[-1]
    return rv


def main(dataset_path: str, config_file: str | None, model: str | None, n_workers: int):
    if config_file is not None:
        assert model is not None, "Model must be provided if config file is provided."
    if model is not None:
        assert config_file is not None, (
            "Config file must be provided if model is provided."
        )

    with open(dataset_path, "r") as f:
        dataset = json.load(f)
    print(f"Found {len(dataset)} task instances to generate instructions for")
    random.seed(24)

    stats = {"completed": 0, "timed_out": 0, "failed": 0}
    repos_to_remove = set()

    try:
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = [
                executor.submit(_process_instance, instance, config_file, model)
                for instance in dataset
            ]

            with logging_redirect_tqdm():
                with tqdm(total=len(dataset), desc="Instances") as pbar:
                    for future in as_completed(futures):
                        result = future.result()
                        for k in stats.keys():
                            stats[k] += result[k]
                        if "cleanup" in result:
                            repos_to_remove.add(result["cleanup"])
                        pbar.set_postfix(stats, refresh=True)
                        pbar.update(1)
    except KeyboardInterrupt:
        print(f"Interrupted. Removing {len(repos_to_remove)} repos")
        if len(repos_to_remove) > 0:
            for repo in repos_to_remove:
                shutil.rmtree(repo, ignore_errors=True)
        return

    # Attach issues to dataset
    for instance in dataset:
        log_dir = (
            LOG_DIR_ISSUE_GEN
            / "from_tests"
            / instance["repo"].split("/")[-1]
            / instance[KEY_INSTANCE_ID]
        )
        path_issue = log_dir / LOG_FILE_ISSUE
        if path_issue.exists():
            instance["problem_statement"] = path_issue.read_text()

    suffix = "ig_tests" if config_file is None else Path(config_file).stem
    with open(
        Path(dataset_path).parent / f"{Path(dataset_path).stem}__{suffix}.json", "w"
    ) as f:
        json.dump(dataset, f, indent=2)
        print(f"Wrote dataset with test code + traces to {f.name}")

    if len(repos_to_remove) > 0:
        print(f"Removing {len(repos_to_remove)} repos")
        for repo in repos_to_remove:
            shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_path", type=str, help="Dataset files to process")
    parser.add_argument(
        "--config_file", type=str, help="Path to the configuration file."
    )
    parser.add_argument("--model", type=str, help="Model to use for rewriting.")
    parser.add_argument(
        "-n", "--n_workers", type=int, default=1, help="Number of workers to use"
    )
    args = parser.parse_args()
    main(**vars(args))
