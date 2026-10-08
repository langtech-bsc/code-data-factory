from types import SimpleNamespace

from swesmith.constants import (
    KEY_PATCH,
    KEY_REFERENCE_PATCH,
    KEY_VERIFIER_DIR,
    RECONSTRUCTION_TESTS_DIR,
    is_reconstruction_instance,
)
from swesmith.harness.eval import gold_patch
from swesmith.harness.utils import _reconstruction_test_command

REPAIR = {KEY_PATCH: "bug.diff"}
RECON = {
    KEY_PATCH: "stub.diff",
    KEY_REFERENCE_PATCH: "restore.diff",
    KEY_VERIFIER_DIR: "/tmp/verifiers/x",
}


def test_reconstruction_instances_are_detected_by_verifier_dir():
    assert is_reconstruction_instance(RECON)
    assert not is_reconstruction_instance(REPAIR)


def test_gold_prediction_is_the_restore_patch_for_reconstruction():
    assert gold_patch(RECON) == "restore.diff"


def test_gold_prediction_is_the_bug_patch_for_repair():
    assert gold_patch(REPAIR) == "bug.diff"


def test_reconstruction_command_runs_hidden_tests_outside_the_repo_config():
    rp = SimpleNamespace(test_cmd="source activate env && pytest -rA")
    command = _reconstruction_test_command(rp)
    assert command.startswith("export PYTHONPATH=/testbed")
    assert "pytest -rA -p no:cacheprovider " + RECONSTRUCTION_TESTS_DIR in command
