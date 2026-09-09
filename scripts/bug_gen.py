#!/usr/bin/env python3
"""Generate, validate, gather, and annotate bugs with Singularity on Slurm.

The command is intentionally filesystem-backed. It can run directly inside
an interactive allocation or be invoked by the dependent Slurm jobs created
by ``scripts/submit_bug_gen.sh``.

Example:
    uv run python scripts/bug_gen.py --language javascript
"""

from __future__ import annotations

import argparse
import json
import os

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from swesmith.constants import LOG_DIR_BUG_GEN, LOG_DIR_RUN_VALIDATION, LOG_DIR_TASKS
from swesmith.profiles import registry
from swesmith.runtime.status import phase_failed, run_statused, status_path


def profiles_for_language(language: str, repos: list[str]) -> list[str]:
    language = language.lower()
    base_names = {
        "c": "CProfile",
        "cpp": "CppProfile",
        "csharp": "CSharpProfile",
        "go": "GoProfile",
        "golang": "GoProfile",
        "java": "JavaProfile",
        "javascript": "JavaScriptProfile",
        "php": "PHPProfile",
        "python": "PythonProfile",
        "ruby": "RubyProfile",
        "rust": "RustProfile",
        "typescript": "TypeScriptProfile",
    }
    base_name = base_names.get(language)
    if base_name is None:
        raise ValueError(f"Unknown language: {language}")
    selected = set(repos)
    values = []
    for profile in registry.values():
        if base_name not in {base.__name__ for base in profile.__class__.__mro__}:
            continue
        if selected and profile.repo_name not in selected and f"{profile.owner}/{profile.repo}" not in selected:
            continue
        values.append(profile.repo_name)
    return sorted(set(values))


def show_stats(repo_names: list[str]) -> None:
    for repo in repo_names:
        bug_dir = LOG_DIR_BUG_GEN / repo
        patch_files = list(bug_dir.rglob("bug__*.diff")) if bug_dir.exists() else []
        validation_dir = LOG_DIR_RUN_VALIDATION / repo
        reports = list(validation_dir.rglob("report.json")) if validation_dir.exists() else []
        print(f"{repo}: generated={len(patch_files)} validated={len(reports)}")


def generate_repo(repo: str, args: argparse.Namespace) -> dict:
    from swesmith.bug_gen.collect_patches import main as collect_patches
    from swesmith.bug_gen.procedural.generate import main as generate

    print(f"\n=== GENERATE {repo} ===", flush=True)
    generate(
        repo=repo,
        max_bugs=args.max_bugs,
        seed=args.seed,
        interleave=args.interleave,
        max_entities=args.max_entities,
        max_candidates=args.max_candidates,
        timeout_seconds=args.timeout_seconds,
    )
    collect_patches(LOG_DIR_BUG_GEN / repo)
    return {"generated_patches": len(list((LOG_DIR_BUG_GEN / repo).rglob("bug__*.diff")))}


def validate_repo(repo: str, args: argparse.Namespace) -> dict:
    from swesmith.harness.valid import main as validate

    patches = LOG_DIR_BUG_GEN / f"{repo}_all_patches.json"
    if not patches.exists():
        print(f"Skipping validation for {repo}: {patches} does not exist", flush=True)
        return {"skipped": True, "reason": "missing patches"}
    print(f"\n=== VALIDATE {repo} ===", flush=True)
    validate(str(patches), args.workers, args.redo_existing)
    return {"reports": len(list((LOG_DIR_RUN_VALIDATION / repo).rglob("report.json")))}


def gather_repo(repo: str, args: argparse.Namespace) -> dict:
    from swesmith.harness.gather import main as gather

    validation_dir = LOG_DIR_RUN_VALIDATION / repo
    if not validation_dir.exists():
        print(f"Skipping gather for {repo}: {validation_dir} does not exist", flush=True)
        return {"skipped": True, "reason": "missing validation"}
    print(f"\n=== GATHER {repo} ===", flush=True)
    gather(
        validation_dir,
        debug_subprocess=args.debug_subprocess,
        override_branch=args.override_branch,
        refresh_sif=False,
        verbose=args.verbose,
    )
    return {"task_file": str(LOG_DIR_TASKS / f"{repo}.json")}


def issue_repo(repo: str, args: argparse.Namespace) -> dict:
    from swesmith.issue_gen.generate import IssueGen

    dataset_path = LOG_DIR_TASKS / f"{repo}.json"
    if not dataset_path.exists():
        print(f"Skipping issue generation for {repo}: {dataset_path} does not exist", flush=True)
        return {"skipped": True, "reason": "missing task instances"}
    print(f"\n=== ISSUE {repo} ({args.issue_model}) ===", flush=True)
    IssueGen(
        Path(args.issue_gen_config),
        workers=args.issue_gen_workers,
        dataset_path=str(dataset_path),
        redo_existing=args.redo_existing,
        model=args.issue_model,
    ).run()
    return {"dataset": str(dataset_path)}


def _run_phase(
    phase: str,
    repos: list[str],
    function: Callable[[str, argparse.Namespace], dict],
    args: argparse.Namespace,
    workers: int,
) -> bool:
    """Run a phase with bounded local workers and durable per-repo status."""
    if not args.redo_existing:
        pending = []
        for repo in repos:
            path = status_path(phase, repo)
            try:
                complete = json.loads(path.read_text(encoding="utf-8")).get("status") == "success"
            except (OSError, json.JSONDecodeError):
                complete = False
            if not complete:
                pending.append(repo)
    else:
        pending = repos

    skipped = len(repos) - len(pending)
    if skipped:
        print(f"{phase}: skipping {skipped} completed repositories")
    if not pending:
        return phase_failed(phase, repos)

    print(f"{phase}: processing {len(pending)} repositories with {workers} worker(s)")

    def run_one(repo: str) -> dict:
        return run_statused(phase, repo, lambda: function(repo, args))

    records = []
    if workers == 1:
        records = [run_one(repo) for repo in pending]
    else:
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f"swesmith-{phase}",
        ) as executor:
            futures = {executor.submit(run_one, repo): repo for repo in pending}
            for future in as_completed(futures):
                records.append(future.result())

    failed = [record for record in records if record.get("status") != "success"]
    if failed:
        print(f"{phase}: {len(failed)} repository failure(s)")
        for record in failed:
            print(f"  {record['repo']}: {record.get('error', 'unknown error')}")
    return phase_failed(phase, repos)


def main(args: argparse.Namespace) -> int:
    repo_names = profiles_for_language(args.language, args.repos)
    if not repo_names:
        raise SystemExit(f"No profiles found for {args.language}")
    print(f"Found {len(repo_names)} repositories for {args.language}")
    if args.show_stats:
        show_stats(repo_names)
        return 0

    phases = [phase.strip() for phase in args.phases.split(",") if phase.strip()]
    unknown = set(phases) - {"gen", "val", "gather", "issue"}
    if unknown:
        raise SystemExit(f"Unknown phases: {sorted(unknown)}")

    if args.status_only:
        return 1 if any(phase_failed(phase, repo_names) for phase in phases) else 0

    phase_functions = {
        "gen": generate_repo,
        "val": validate_repo,
        "gather": gather_repo,
        "issue": issue_repo,
    }
    phase_failures = False
    for phase in phases:
        phase_workers = args.repo_workers if phase == "gen" else 1
        phase_failures |= _run_phase(
            phase,
            repo_names,
            phase_functions[phase],
            args,
            max(1, phase_workers),
        )
    return 1 if phase_failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="javascript")
    parser.add_argument(
        "--repos",
        type=lambda value: [x.strip() for x in value.split(",") if x.strip()],
        default=[],
    )
    parser.add_argument("--phases", default="gen,val,gather,issue")
    parser.add_argument("--max-bugs", type=int, default=200)
    parser.add_argument("--max-entities", type=int, default=2000)
    parser.add_argument("--max-candidates", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--timeout-seconds", type=int, default=None)
    parser.add_argument("--interleave", action="store_true")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("MAX_WORKERS", "1")))
    parser.add_argument("--repo-workers", type=int, default=int(os.environ.get("SWESMITH_REPO_WORKERS", "4")))
    parser.add_argument("--issue-gen-workers", type=int, default=4)
    parser.add_argument("--issue-gen-config", default="configs/issue_gen/ig_v2.yaml")
    parser.add_argument("--issue-model", default="glm-5.3-flash")
    parser.add_argument("--redo-existing", action="store_true")
    parser.add_argument("--show-stats", action="store_true")
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Check durable phase status without running any work",
    )
    parser.add_argument("--override-branch", action="store_true")
    parser.add_argument("--debug-subprocess", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
