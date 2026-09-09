"""Runtime helpers for executing repository tasks without a container daemon."""

from .singularity import (
    ExecResult,
    SingularityRunner,
    get_singularity_command,
    make_workspace,
    resolve_sif,
)

__all__ = [
    "ExecResult",
    "SingularityRunner",
    "get_singularity_command",
    "make_workspace",
    "resolve_sif",
]
