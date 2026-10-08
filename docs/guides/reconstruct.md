# Source-only reconstruction tasks

Reconstruction tasks follow the CodeMidas method: blank out working functions, give the solver a written contract, and grade with hidden tests that were checked against the original code. Unlike bug tasks, the solver never sees a bug diff. It starts from the stubbed source and must rebuild the removed behavior.

This guide covers Python profiles only. Each task needs a prebuilt SIF for its repository, because every control run executes inside Singularity.

---

## Pipeline

For each candidate anchor (public function or public method, shuffled with a fixed seed):

| Stage | What runs | Gate |
|---|---|---|
| Anchor | `PythonEntity` from the existing adapter, filtered to public, at least 4 lines, complexity at least 2, no tab indentation | Static |
| Design | Agent with `exec` and terminal `submit` or `reject`. Picks up to 3 same-file functions and writes an instruction plus `REQ-n` requirements | Shape of the reply, same-file selection |
| Stub | `PythonEntity.stub` replaces each def..end span. Decorators and signatures stay in the file | `ast.parse` on the stubbed file |
| Tests | Agent writes one `test_reconstruction.py`. Every test cites a `REQ-n` and every requirement is covered | Static mapping check, then the reference gate (up to 3 rounds) |
| Review | One audit call returns `sound`, `revise` (with a corrected test file, re-gated once), or `reject` | Verdict |
| Controls | 2 start runs (stub, must fail) and 4 reference runs (original, must pass) | `derive_fail_to_pass` |

A candidate is accepted only when FAIL_TO_PASS is non-empty and no control is flaky. A test that passes on the stub, fails on the reference, or differs across runs of the same control rejects the candidate. Rejections are logged with reasons.

Hidden tests are mounted read-only at `/hidden_tests` and run with `PYTHONPATH=/testbed`, so the repository's own pytest configuration is ignored.

## Running it

Locally, inside an allocation that has a SIF for the repository and access to the PTIE endpoint:

```bash
uv run python scripts/bug_gen.py --language python --repos <owner__repo> --phases reconstruct \
  --reconstruct-target 5 --reconstruct-max-candidates 50
```

On MN5, reconstruction is opt-in and runs on a GPP allocation:

```bash
bash scripts/submit_bug_gen.sh --language python --repos <owner__repo> --phases reconstruct
```

The phase skips repositories that are not Python profiles or that lack a SIF, and records the outcome in the usual per-repo status files.

## Outputs

Under `$SWESMITH_WORK_ROOT/logs`:

- `task_insts/<repo>__reconstruct.json`: accepted task records
- `reconstruct/<repo>/rejections.jsonl`: one line per rejected anchor with its reason
- `reconstruct/<repo>/verifiers/<instance_id>/test_reconstruction.py`: the hidden test file
- `reconstruct/<repo>/runs/`: per-run logs and `test_output.txt` for every control and gate run

Each accepted record has the standard harness fields plus:

- `patch`: the stub diff (solver start)
- `reference_patch`: the inverse diff that restores the original code
- `verifier_dir`: absolute path mounted read-only at `/hidden_tests`
- `FAIL_TO_PASS`, `PASS_TO_PASS` (always empty for this task type)
- `problem_statement`, `requirements`, `selected_entities`, `review`, `controls`

Grading uses the same harness as bug tasks. Gold predictions for reconstruction use `reference_patch`, and a solver's patch is applied after the stub. See [Validation & Evaluation](harnesses.md).

## Limits and what is not covered yet

- Python only. Tab-indented files are skipped by design.
- Multi-file selections are not supported. Each candidate stays inside one file.
- The reference gate and controls depend on the repository's test command producing parseable per-test status lines. Repositories whose log parser misses test ids will produce rejections, not bad tasks.
- The LLM and the container are both required. Unit tests cover the pure stages (stub diffs, mapping, fail-to-pass derivation, the action loop, and eval routing). The container and LLM stages still need an end-to-end run on MN5 before the output is trusted at scale.
- Solver-side evaluation of CodeMidas-style reconstructions at scale, and the adversarial leak check from the paper, are not implemented.
