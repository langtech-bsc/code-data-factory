from pathlib import Path

from swesmith.runtime.singularity import SingularityRunner, resolve_sif


def test_resolve_sif_uses_shared_cache(tmp_path: Path):
    assert resolve_sif("swebench/swesmith.x86_64:latest", tmp_path) == (
        tmp_path / "swesmith.x86_64_latest.sif"
    )


def test_runner_builds_isolated_exec_command(tmp_path: Path):
    image = tmp_path / "reference.sif"
    image.touch()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    runner = SingularityRunner(image, workspace, singularity_command="/bin/echo")

    result = runner.exec_run("printf ready")

    assert result.exit_code == 0
    assert "--cleanenv" in result.output.decode()
    assert f"{workspace}:/testbed" in result.output.decode()
    assert str(image) in result.output.decode()
