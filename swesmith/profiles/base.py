"""
Base repository profile class.

This module defines the abstract base class for repository profiles that specify
installation and testing configurations for different repositories.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request

from abc import ABC, abstractmethod, ABCMeta
from collections import UserDict
from dataclasses import dataclass, field
from dotenv import load_dotenv
from functools import cached_property
from ghapi.all import GhApi
from pathlib import Path
from threading import Lock as ThreadLock

# Note: swesmith.bug_gen.adapters is imported lazily in extract_entities() to avoid
# loading tree-sitter dependencies when only using Registry/get_valid_report
from swesmith.harness.constants import FAIL_TO_PASS
from swesmith.constants import (
    KEY_INSTANCE_ID,
    KEY_PATCH,
    LOG_DIR_ENV,
    ORG_NAME_DH,
    ORG_NAME_GH,
    INSTANCE_REF,
    CodeEntity,
)
from swesmith.runtime.definition import translate_recipe
from swesmith.runtime.singularity import (
    SingularityRunner,
    get_singularity_command,
    make_workspace,
    resolve_sif,
)
from unidiff import PatchSet


load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_SSH_KEYS = ["id_rsa", "id_ecdsa", "id_ecdsa_sk", "id_ed25519", "id_ed25519_sk"]


def _find_ssh_key() -> Path | None:
    """Find an SSH private key: explicit env var first, then default paths."""
    key_path = os.getenv("GITHUB_USER_SSH_KEY")
    if key_path and Path(key_path).exists():
        return Path(key_path)

    ssh_dir = Path.home() / ".ssh"
    for key_name in _DEFAULT_SSH_KEYS:
        key_file = ssh_dir / key_name
        if key_file.exists():
            return key_file

    return None


class SingletonMeta(ABCMeta):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


@dataclass
class RepoProfile(ABC, metaclass=SingletonMeta):
    """
    Base class for repository profiles that define installation and testing specifications.

    This class provides a language-agnostic interface for repository configuration,
    allowing different languages (Python, Go, Rust, etc.) to have their own
    installation and testing patterns while maintaining a consistent API.
    """

    org_dh: str = ORG_NAME_DH
    org_gh: str = ORG_NAME_GH
    arch: str = "x86_64" if platform.machine() not in {"aarch64", "arm64"} else "arm64"

    @property
    def pltf(self) -> str:
        if self.arch == "x86_64":
            return "linux/x86_64"
        elif self.arch == "arm64":
            return "linux/arm64/v8"
        else:
            raise ValueError(
                f"Architecture {self.arch} not supported. Must be one of ['x86_64', 'arm64']"
            )

    exts: list[str] = field(default_factory=list)  # Must be set by subclass
    eval_sets: set[str] = field(default_factory=set)

    # Install + Test specifications
    timeout: int = 90  # timeout (sec) for running test suite for a single instance
    timeout_ref: int = 900  # timeout for running entire test suite

    # `min_testing`: If set, then subset of tests (not all) are run for post-bug validation
    # Affects get_test_cmd, get_valid_report
    min_testing: bool = False

    # `min_pregold`: If set, then for pre-bug validation, individual runs are
    # performed instead of running the entire test suite
    # Affects valid.py
    min_pregold: bool = False

    # Per-profile lock to prevent concurrent clone/cache initialization races.
    # We intentionally use threading.Lock (not multiprocessing.Lock) to avoid
    # allocating OS semaphores for every registered profile instance.
    _lock: object = field(
        default_factory=ThreadLock, init=False, repr=False, compare=False
    )

    # GitHub API instance (lazily initialized)
    _api: GhApi | None = field(default=None, init=False, repr=False, compare=False)

    # Class-level caches
    _cache_test_paths = None
    _cache_branches = None
    _cache_mirror_exists = None
    _cache_repo_private: bool | None = field(
        default=None, init=False, repr=False, compare=False
    )

    ### START: Properties, Methods that *do not* require (re-)implementation ###

    @property
    def api(self) -> GhApi:
        """Get GitHub API instance with lazy initialization."""
        if self._api is None:
            token = os.getenv("GITHUB_TOKEN")
            self._api = GhApi(token=token)
        return self._api

    def _is_repo_private(self) -> bool:
        if self._cache_repo_private is not None:
            return self._cache_repo_private
        try:
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}"
            headers = {"User-Agent": "swesmith"}
            token = os.getenv("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"token {token}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
                self._cache_repo_private = data.get("private", False)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.warning(
                    "Repo '%s/%s' returned 404 — assuming private",
                    self.owner,
                    self.repo,
                )
                self._cache_repo_private = True
            else:
                raise
        return self._cache_repo_private

    @staticmethod
    def _configure_ssh_env():
        """Bake GIT_SSH_COMMAND into os.environ if GITHUB_USER_SSH_KEY is set."""
        key_path = os.getenv("GITHUB_USER_SSH_KEY")
        if key_path and "GIT_SSH_COMMAND" not in os.environ:
            os.environ["GIT_SSH_COMMAND"] = f"ssh -i {key_path} -o IdentitiesOnly=yes"

    @property
    def mirror_url(self) -> str:
        if self._is_repo_private():
            return f"git@github.com:{self.mirror_name}.git"
        return f"https://github.com/{self.mirror_name}"

    @property
    def _mirror_ssh_url(self) -> str:
        return f"git@github.com:{self.mirror_name}.git"

    @property
    def _source_read_url(self) -> str:
        if self._is_repo_private():
            return f"git@github.com:{self.owner}/{self.repo}.git"
        return f"https://github.com/{self.owner}/{self.repo}.git"

    @property
    def image_name(self) -> str:
        return f"{self.org_dh}/swesmith.{self.arch}.{self.owner}_1776_{self.repo}.{self.commit[:8]}".lower()

    @cached_property
    def _cache_image_exists(self) -> bool:
        """Check whether the profile's SIF exists in the local cache."""
        return self.image_path.exists()

    @property
    def image_path(self) -> Path:
        return resolve_sif(self.image_name)

    @property
    def mirror_name(self):
        return f"{self.org_gh}/{self.repo_name}"

    @property
    def repo_name(self):
        return f"{self.owner}__{self.repo}.{self.commit[:8]}"

    @property
    def branches(self):
        """Get task instance branches corresponding to this repo"""
        if self._cache_branches is None:
            self._cache_branches = []
            increase, page = 0, 1
            while page == 1 or increase > 0:
                prev = len(self._cache_branches)
                self._cache_branches.extend(
                    self.api.repos.list_branches(
                        owner=self.org_gh,
                        repo=self.repo_name,
                        prefix=self.repo_name,
                        per_page=100,
                        page=page,
                    )
                )
                increase = len(self._cache_branches) - prev
                page += 1
            self._cache_branches = [
                b.name
                for b in self._cache_branches
                if b.name.startswith(self.repo_name)
            ]
        return self._cache_branches

    def _get_cached_test_paths(self) -> list[Path]:
        """Clone the repo, get all testing file paths relative to the repo directory, then clean up."""
        if self._cache_test_paths is None:
            with self._lock:  # Only one process enters this block at a time
                # Use unique temp dir to avoid race conditions in multiprocessing
                import uuid

                temp_dest = f"{self.repo_name}_{uuid.uuid4().hex[:8]}"
                dir_path, cloned = self.clone(dest=temp_dest)
                self._cache_test_paths = [
                    Path(os.path.relpath(os.path.join(root, file), dir_path))
                    for root, _, files in os.walk(Path(dir_path).resolve())
                    for file in files
                    if self._is_test_path(root, file)
                ]
                if cloned:
                    shutil.rmtree(dir_path)

        return self._cache_test_paths

    def _mirror_exists(self):
        """Check if mirror repository exists under organization"""
        if self._cache_mirror_exists is not True:
            try:
                self.api.repos.get(owner=self.org_gh, repo=self.repo_name)
                self._cache_mirror_exists = True
            except:
                self._cache_mirror_exists = False
        return self._cache_mirror_exists

    def _prepare_recipe(self, content: str) -> str:
        """Return the profile recipe before Apptainer translation."""
        return content

    def _build_sif(self, recipe: str, context_dir: Path) -> Path:
        """Translate and build a profile recipe as a local SIF."""
        env_dir = context_dir
        definition_path = env_dir / "Singularity.def"
        definition_path.write_text(translate_recipe(recipe, context_dir=env_dir))
        destination = self.image_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with open(env_dir / "build_image.log", "w") as log_file:
            subprocess.run(
                [
                    get_singularity_command(),
                    "build",
                    "--fakeroot",
                    str(temporary),
                    str(definition_path),
                ],
                check=True,
                cwd=env_dir,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        temporary.replace(destination)
        self._cache_image_exists = True
        return destination

    def build_image(self):
        """Build a Singularity image for this repository profile."""
        env_dir = LOG_DIR_ENV / self.repo_name
        env_dir.mkdir(parents=True, exist_ok=True)
        self._build_sif(self.recipe, env_dir)

    def create_mirror(self):
        """Create a mirror of this repository at the specified commit."""
        if self._mirror_exists():
            return
        if self.repo_name in os.listdir():
            shutil.rmtree(self.repo_name)
        source_repo = self.api.repos.get(self.owner, self.repo)
        self.api.repos.create_in_org(
            self.org_gh, self.repo_name, private=source_repo.private
        )

        # Clone the source repository (READ operation)
        self._configure_ssh_env()
        subprocess.run(
            f"git clone {self._source_read_url} {self.repo_name}",
            shell=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Build the git commands
        git_cmds = [
            f"cd {self.repo_name}",
            f"git checkout {self.commit}",
        ]

        # Add submodule update if submodules exist
        if os.path.exists(os.path.join(self.repo_name, ".gitmodules")):
            git_cmds.append("git submodule update --init --recursive")

        # Add the rest of the commands (WRITE → always SSH)
        git_cmds.extend(
            [
                "rm -rf .git",
                "git init",
                'git config user.name "swesmith"',
                'git config user.email "swesmith@anon.com"',
                "rm -rf .github/workflows",
                "rm -rf .github/dependabot.y*",
                "mv .gitignore .gitignore.bak 2>/dev/null; true",  # <-- temporarily hide .gitignore
                "git add .",
                "mv .gitignore.bak .gitignore 2>/dev/null; true",  # <-- restore it
                "git add -f .gitignore 2>/dev/null; true",  # <-- force-add .gitignore itself
                "git commit --no-gpg-sign -m 'Initial commit'",
                "git branch -M main",
                f"git remote add origin git@github.com:{self.mirror_name}.git",
                "git push -u origin main",
            ]
        )

        # Execute the commands
        subprocess.run(
            "; ".join(git_cmds),
            shell=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Clean up
        subprocess.run(
            f"rm -rf {self.repo_name}",
            shell=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def clone(self, dest: str | None = None) -> tuple[str, bool]:
        """Clone repository locally"""
        if not self._mirror_exists():
            raise ValueError(
                "Mirror clone repo must be created first (call .create_mirror)"
            )
        dest = self.repo_name if not dest else dest
        if not os.path.exists(dest):
            self._configure_ssh_env()
            clone_cmd = f"git clone {self.mirror_url} {dest}"
            subprocess.run(
                clone_cmd,
                check=True,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Always set SSH push URL (writes always use SSH)
            subprocess.run(
                f"git -C {dest} remote set-url --push origin {self._mirror_ssh_url}",
                check=True,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return dest, True
        else:
            return dest, False

    def extract_entities(
        self,
        dirs_exclude: list[str] = [],
        dirs_include: list[str] = [],
        files_exclude: list[str] = [],
        exclude_tests: bool = True,
        max_entities: int = -1,
    ) -> list[CodeEntity]:
        """
        Extracts entities (functions, classes, etc.) from files in a directory.
        Args:
            directory_path (str): Path to the directory to scan.
            exclude_tests (bool): Whether to exclude test files and directories.
        Returns:
            List[CodeEntity]: List of CodeEntity objects containing entity information.
        """
        # Lazy import to avoid loading tree-sitter dependencies when not needed
        from swesmith.bug_gen.adapters import get_entities_from_file

        if not self.exts:
            raise ValueError(
                f"RepoProfile subclass {self.__class__.__name__} must provide 'exts' list for entity extraction."
            )

        dir_path, cloned = self.clone()
        entities = []
        for root, _, files in os.walk(dir_path):
            for file in files:
                if exclude_tests and self._is_test_path(root, file):
                    continue
                if dirs_exclude and any([x in root for x in dirs_exclude]):
                    continue
                if dirs_include and not any([x in root for x in dirs_include]):
                    continue

                file_path = os.path.join(root, file)
                rel_file_path = "/" + os.path.relpath(file_path, dir_path).replace(
                    os.sep, "/"
                )
                if files_exclude and rel_file_path in files_exclude:
                    continue

                try:
                    open(file_path, "r", encoding="utf-8").close()
                except:
                    continue

                file_ext = Path(file_path).suffix
                if file_ext not in self.exts:
                    continue
                get_entities_from_file[file_ext](entities, file_path, max_entities)
        if cloned:
            shutil.rmtree(dir_path)
        return entities

    def get_container(self, instance: dict) -> SingularityRunner:
        """Return a Singularity runner with the task instance initialized."""
        import uuid

        self.require_image()
        instance_id = instance[KEY_INSTANCE_ID]
        workspace_root = make_workspace(prefix=f"{instance_id}.{uuid.uuid4().hex[:8]}-")
        workspace = workspace_root / "repo"
        try:
            self.clone(dest=str(workspace))
        except Exception:
            shutil.rmtree(workspace_root, ignore_errors=True)
            raise
        runner = SingularityRunner(self.image_path, workspace)
        runner._owned_workspace = workspace_root
        val = runner.exec_run(f"git checkout {instance_id}")
        if val.exit_code != 0:
            runner.close()
            raise RuntimeError(
                f"Failed to checkout instance {instance_id} in Singularity workspace: {val.output.decode()}"
            )
        return runner

    def require_image(self) -> Path:
        """Require a prebuilt SIF and explain how to create it when missing."""
        image = self.image_path
        if not image.exists():
            raise FileNotFoundError(
                f"Missing Singularity image for {self.repo_name}: {image}. "
                "Run the dedicated image-build job or "
                f"`uv run python -m swesmith.build_repo.create_images --repos {self.repo_name} -y`."
            )
        self._cache_image_exists = True
        return image

    def download_image(self) -> Path:
        """Explicitly download the OCI image into the local SIF cache."""
        destination = resolve_sif(self.image_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._cache_image_exists = True
            return destination
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            subprocess.run(
                [
                    get_singularity_command(),
                    "pull",
                    str(temporary),
                    f"docker://{self.image_name}",
                ],
                check=True,
            )
            temporary.replace(destination)
            self._cache_image_exists = True
            return destination
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Failed to download Singularity image {self.image_name}: {exc}"
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def push_image(self, rebuild_image: bool = False):
        """Build/update the shared SIF cache.

        Remote registry publication is intentionally unsupported in the
        Singularity runtime.  The method remains as a compatibility shim for
        callers that used the old Docker workflow.
        """
        if rebuild_image:
            self._cache_image_exists = False
            self.build_image()
        if not self._cache_image_exists:
            self.require_image()

    def set_github_token(self, token: str):
        """Set a custom GitHub token and reset the API instance."""
        self._api = GhApi(token=token)

    ### END: Properties, Methods that *do not* require (re-)implementation ###

    ### START: Properties, Methods that *may* require (re-)implementation ###

    def _is_test_path(self, root: str, file: str) -> bool:
        """Check whether the file path corresponds to a testing related file"""
        if len(self.exts) > 1 and not any([file.endswith(ext) for ext in self.exts]):
            return False
        if file.lower().startswith("test") or file.rsplit(".", 1)[0].endswith("test"):
            return True
        dirs = root.split("/")
        if any([x in dirs for x in ["tests", "test", "specs"]]):
            return True
        return False

    def get_test_files(self, instance: dict) -> tuple[list[str], list[str]]:
        """Given an instance, return files corresponding to F2P, P2P test files"""
        return [], []

    def get_test_cmd(
        self, instance: dict, f2p_only: bool = False
    ) -> tuple[str, list[Path]]:
        assert instance[KEY_INSTANCE_ID].rsplit(".", 1)[0] == self.repo_name, (
            f"WARNING: {instance[KEY_INSTANCE_ID]} not from {self.repo_name}"
        )
        test_command = self.test_cmd

        if f2p_only:
            f2p_files, _ = self.get_test_files(instance)
            test_command += f" {' '.join(f2p_files)}"
            return test_command, f2p_files

        if self.min_testing and FAIL_TO_PASS in instance:
            f2p_files, p2p_files = self.get_test_files(instance)
            if len(f2p_files + p2p_files) > 0:
                test_command += f" {' '.join(f2p_files + p2p_files)}"
            return test_command, f2p_files + p2p_files

        if not self.min_testing or KEY_PATCH not in instance:
            # If min testing is not enabled or there's no patch
            # return test command as is (usually = run whole test suite)
            return test_command, []

        # Get all testing related file paths in the repo
        test_paths = self._get_cached_test_paths()

        # For PR Mirroring (SWE-bench style) instances
        if (
            INSTANCE_REF in instance
            and len(instance[INSTANCE_REF]["test_patch"].strip()) > 0
        ):
            # if test patch is available, use that information
            test_patch = instance[INSTANCE_REF]["test_patch"]
            rv = []
            for x in PatchSet(test_patch):
                for test_path in test_paths:
                    if str(test_path).endswith(x.path) or str(test_path).endswith(
                        Path(x.path).name
                    ):
                        rv.append(test_path)
            if len(rv) > 0:
                test_command += f" {' '.join([str(v) for v in rv])}"
                return test_command, rv

        # Identify relevant test files based on the patch
        patch_paths = [Path(f.path) for f in PatchSet(instance[KEY_PATCH])]
        rv = []
        for pp in patch_paths:
            for test_path in test_paths:
                # Check for common test file naming conventions first
                # If found, add to list and break
                common_test_names = [
                    f"test_{pp.stem}{pp.suffix}",
                    f"test{pp.stem}{pp.suffix}",
                    f"{pp.stem}_test{pp.suffix}",
                    f"{pp.stem}test{pp.suffix}",
                ]
                if any([str(test_path).endswith(name) for name in common_test_names]):
                    rv.append(test_path)
                    break
            else:
                for test_path in test_paths:
                    if pp.parent.name == test_path.parent.name:
                        # If similar testing folder found, add to list and break
                        rv.append(test_path.parent)
                        break
                    elif any(
                        [
                            test_path.stem
                            in {
                                f"test_{pp.parent.name}",
                                f"test{pp.parent.name}",
                                f"{pp.parent.name}_test",
                                f"{pp.parent.name}test",
                            }
                        ]
                    ):
                        rv.append(test_path)

        if len(rv) > 0:
            # Remove duplicates
            test_files = [x for x in rv if x.is_file()]
            final = [x for x in rv if not x.is_file()]
            for test_file in test_files:
                if os.path.dirname(test_file) not in final:
                    final.append(test_file)
            test_command += f" {' '.join(sorted([str(v) for v in set((final))]))}"

        return test_command, rv

    ### END: Properties, Methods that *may* require (re-)implementation ###

    ### START: Properties, Methods that require implementation ###

    owner: str = ""
    repo: str = ""
    commit: str = ""
    test_cmd: str = ""

    @abstractmethod
    def log_parser(self, log: str) -> dict[str, str]:
        """Parse test output logs and extract relevant information."""
        pass

    @property
    def recipe(self) -> str:
        """Return the recipe-compatible recipe for this profile."""
        pass

    ### END: Properties, Methods that require implementation ###


### MARK: Profile Registry ###


class Registry(UserDict):
    """A registry mapping repo/mirror names to RepoProfile subclasses."""

    def __init__(self, github_token: str | None = None):
        super().__init__()
        self.github_token = github_token

    def register_profile(self, profile_class: type):
        """Register a RepoProfile subclass (except base types)."""
        # Skip base types
        if profile_class.__name__ in {
            "RepoProfile",
            "PythonProfile",
            "GoProfile",
            "RubyProfile",
            "RustProfile",
        }:
            # TODO: Update for new languages
            return
        # Create temporary instance to get properties
        p = profile_class()
        self.data[p.repo_name] = profile_class
        self.data[p.mirror_name] = profile_class

    def get(self, key: str) -> RepoProfile:
        """Get a profile class by mirror name or repo name."""
        cls = self.data.get(key)
        if cls is None:
            raise KeyError(f"No profile registered for key: {key}")
        profile = cls()
        if self.github_token:
            profile.set_github_token(self.github_token)
        return profile

    def get_from_inst(self, instance: dict) -> RepoProfile:
        """Get a profile class by a SWE-smith instance dict."""
        key = instance.get("repo", instance[KEY_INSTANCE_ID].rsplit(".", 1)[0])
        return self.get(key)

    def keys(self):
        return self.data.keys()

    def values(self):
        profiles = []
        for cls in set(self.data.values()):
            profile = cls()
            if self.github_token:
                profile.set_github_token(self.github_token)
            profiles.append(profile)
        return profiles

    def set_github_token(self, token: str):
        """Set GitHub token for all profiles retrieved from this registry."""
        self.github_token = token


# Global registry instance that can be shared across modules
registry = Registry()
