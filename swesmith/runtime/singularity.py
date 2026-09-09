"""Small Apptainer/Singularity runtime used by the SWE-smith harness.

The runner deliberately starts one short-lived ``singularity exec`` process per
command.  Repository state lives in a host workspace mounted at ``/testbed``;
this gives callers the same stateful behaviour they need without requiring a
container daemon.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_CONTAINER_WORKDIR = "/testbed"
DEFAULT_TMP_ROOT = Path(os.environ.get("SWESMITH_TMP_ROOT", tempfile.gettempdir()))
DEFAULT_SIF_ROOT = Path(
    os.environ.get(
        "SWESMITH_SIF_ROOT",
        "/gpfs/scratch/bsc88/post-training/singularities/code-data-factory",
    )
)
SINGULARITY_CANDIDATES = (
    "/apps/GPP/SINGULARITY/4.1.5/bin/singularity",
    "/apps/GPP/SINGULARITY/3.11.5/bin/singularity",
)


@dataclass(frozen=True)
class ExecResult:
    """Result shape used by the harness execution code."""

    exit_code: int
    output: bytes
    runtime: float = 0.0
    timed_out: bool = False


def get_singularity_command() -> str:
    """Return the available Singularity/Apptainer executable."""
    for candidate in SINGULARITY_CANDIDATES:
        if Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    executable = shutil.which("singularity") or shutil.which("apptainer")
    if executable:
        return executable
    raise RuntimeError(
        "Singularity/Apptainer was not found. Load the MN5 singularity module "
        "or set SINGULARITY_COMMAND to an executable path."
    )


def _command_text(command: str | Sequence[str]) -> str:
    if isinstance(command, str):
        return command
    return shlex.join(str(part) for part in command)


def _safe_mount_source(path: Path) -> str:
    return str(path.expanduser().resolve())


def resolve_sif(image_name: str, sif_root: Path | None = None) -> Path:
    """Resolve a logical profile image name to a local SIF file.

    Existing absolute/relative SIF paths are accepted.  Otherwise the logical
    OCI image name is mapped to a cache filename.  The caller can use
    :func:`SingularityRunner.pull` to populate a missing cache entry.
    """
    candidate = Path(image_name).expanduser()
    if candidate.suffix == ".sif" or candidate.is_absolute():
        return candidate
    root = (
        sif_root
        or Path(
            os.environ.get("SWESMITH_SIF_ROOT", str(DEFAULT_SIF_ROOT))
        )
    ).expanduser()
    return root / f"{image_name.rsplit('/', 1)[-1].replace(':', '_')}.sif"


def make_workspace(prefix: str = "swesmith-") -> Path:
    """Create a temporary workspace on node-local storage when available."""
    root = Path(os.environ.get("SWESMITH_TMP_ROOT", str(DEFAULT_TMP_ROOT))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


class SingularityRunner:
    """Execute commands in a SIF with a mounted repository workspace."""

    def __init__(
        self,
        image: str | Path,
        workspace: str | Path,
        *,
        container_workdir: str = DEFAULT_CONTAINER_WORKDIR,
        environment: Mapping[str, str] | None = None,
        singularity_command: str | None = None,
        extra_binds: Sequence[tuple[str | Path, str, str | None]] = (),
    ) -> None:
        self.image = Path(image).expanduser().resolve()
        self.workspace = Path(workspace).expanduser().resolve()
        self.container_workdir = container_workdir
        self.environment = dict(environment or {})
        self.singularity_command = singularity_command or os.environ.get(
            "SINGULARITY_COMMAND"
        )
        self.extra_binds = tuple(extra_binds)
        if not self.image.exists():
            raise FileNotFoundError(
                f"Singularity image does not exist: {self.image}. "
                "Build or pull it before running validation."
            )
        if not self.workspace.exists():
            raise FileNotFoundError(f"Singularity workspace does not exist: {self.workspace}")

    @property
    def command(self) -> str:
        if self.singularity_command:
            return self.singularity_command
        return get_singularity_command()

    def close(self) -> None:
        """Remove a workspace created by ``RepoProfile.get_container``."""
        owned_workspace = getattr(self, "_owned_workspace", None)
        if owned_workspace is not None:
            shutil.rmtree(owned_workspace, ignore_errors=True)

    def _argv(
        self,
        command: str | Sequence[str],
        *,
        workdir: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> list[str]:
        argv = [
            self.command,
            "exec",
            "--cleanenv",
            "--containall",
            "--no-home",
            "--no-mount",
            "bind-paths",
            "--bind",
            f"{_safe_mount_source(self.workspace)}:{self.container_workdir}",
        ]
        for source, destination, mode in self.extra_binds:
            mount = f"{_safe_mount_source(Path(source))}:{destination}"
            if mode:
                mount += f":{mode}"
            argv.extend(["--bind", mount])

        merged_env = dict(self.environment)
        merged_env.update(environment or {})
        for key, value in merged_env.items():
            argv.extend(["--env", f"{key}={value}"])

        argv.extend([str(self.image), "bash", "--noprofile", "--norc", "-lc"])
        command_text = _command_text(command)
        cwd = workdir or self.container_workdir
        argv.append(f"cd {shlex.quote(cwd)} && exec {command_text}")
        return argv

    def exec_run(
        self,
        command: str | Sequence[str],
        *,
        workdir: str | None = None,
        user: str | None = None,
        environment: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a command and return captured combined output."""
        del user  # Singularity uses the invoking UID; root is not required.
        started = time.monotonic()
        process = subprocess.Popen(
            self._argv(command, workdir=workdir, environment=environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
            output = (exc.output or b"") + (output or b"")
            timed_out = True
        return ExecResult(
            exit_code=process.returncode if not timed_out else -signal.SIGTERM,
            output=output or b"",
            runtime=time.monotonic() - started,
            timed_out=timed_out,
        )

    def pull(self, image_name: str, sif_root: Path | None = None) -> Path:
        """Pull an OCI image into the local SIF cache."""
        destination = resolve_sif(image_name, sif_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return destination
        temporary = Path(tempfile.mkstemp(prefix=".pull-", suffix=".sif", dir=destination.parent)[1])
        temporary.unlink()
        try:
            subprocess.run(
                [self.command, "pull", str(temporary), f"docker://{image_name}"],
                check=True,
            )
            if not temporary.exists():
                raise RuntimeError(f"Singularity pull did not create {temporary}")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
