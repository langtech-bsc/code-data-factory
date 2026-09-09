"""
Purpose: Automated construction of Singularity images for repositories using profile registry.

Usage: uv run python -m swesmith.build_repo.create_images --workers 4 --repos django --proceed
"""

import argparse
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

from swesmith.profiles import registry


def build_profile_image(profile, publish=False):
    """
    Build a Singularity image for a specific profile.

    Args:
        profile: A RepoProfile instance

    Returns:
        tuple: (profile_name, success: bool, error_message: str)
    """
    try:
        profile.create_mirror()
        profile.build_image()
        if publish:
            profile.push_image()
        return (profile.image_name, True, None)
    except Exception as e:
        error_msg = f"Error building {profile.image_name}: {str(e)}"
        return (profile.image_name, False, error_msg)


def build_all_images(
    workers=4,
    repo_filter=None,
    language=None,
    proceed=False,
    publish=False,
    force=False,
    arch=None,
):
    """
    Build Singularity images for all registered profiles in parallel.

    Args:
        workers: Maximum number of parallel workers
        repo_filter: Optional list of repository name patterns to filter by (fuzzy matching)
        proceed: Whether to proceed without confirmation
        force: Force rebuild even if image already exists
        arch: Architecture string to build for (e.g. "x86_64", "arm64")

    Returns:
        tuple: (successful_builds, failed_builds)
    """
    base_names = {
        "c": "CProfile",
        "cpp": "CppProfile",
        "csharp": "CSharpProfile",
        "go": "GoProfile",
        "golang": "GoProfile",
        "java": "JavaProfile",
        "javascript": "JavaScriptProfile",
        "php": "PhpProfile",
        "python": "PythonProfile",
        "ruby": "RubyProfile",
        "rust": "RustProfile",
        "typescript": "TypeScriptProfile",
    }
    base_name = base_names.get(language.lower()) if language else None
    all_profiles = [
        profile
        for profile in registry.values()
        if base_name is None
        or base_name in {base.__name__ for base in profile.__class__.__mro__}
    ]

    # Update profile architecture if specified
    if arch:
        target_arch = arch
        print(f"Forcing build for architecture: {target_arch}")
        for profile in all_profiles:
            profile.arch = target_arch

    # Remove environments that have already been built
    # Filter out profiles that already have images built (unless force is enabled)
    profiles_to_build = []
    if not force:
        for profile in all_profiles:
            if not profile.image_path.exists():
                profiles_to_build.append(profile)
    else:
        profiles_to_build = list(all_profiles)

    # Filter profiles if specified (fuzzy matching)
    if repo_filter:
        filtered_profiles = []
        for profile in profiles_to_build:
            haystack = " ".join(
                [
                    profile.image_name,
                    profile.repo_name,
                    f"{profile.owner}/{profile.repo}",
                ]
            ).lower()
            if any(pattern.lower() in haystack for pattern in repo_filter):
                filtered_profiles.append(profile)
        profiles_to_build = filtered_profiles

    if not profiles_to_build:
        print("No profiles to build.")
        return [], []

    # Deduplicate profiles_to_build by image_name (more efficiently)
    profiles_to_build = list(
        OrderedDict(
            (profile.image_name, profile) for profile in profiles_to_build
        ).values()
    )

    print("Profiles to build:")
    for profile in sorted(profiles_to_build, key=lambda p: p.image_name):
        print(f"- {profile.image_name}")

    if not proceed:
        proceed = (
            input(
                f"Proceed with building {len(profiles_to_build)} images? (y/n): "
            ).lower()
            == "y"
        )
    if not proceed:
        return [], []

    # Build images in parallel
    successful, failed = [], []

    with tqdm(
        total=len(profiles_to_build), smoothing=0, desc="Building environment images"
    ) as pbar:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # Submit all build tasks
            future_to_profile = {
                executor.submit(build_profile_image, profile, publish): profile
                for profile in profiles_to_build
            }

            # Process completed tasks
            for future in as_completed(future_to_profile):
                pbar.update(1)
                profile_name, success, error_msg = future.result()

                if success:
                    successful.append(profile_name)
                else:
                    failed.append(profile_name)
                    if error_msg:
                        print(f"\n{error_msg}")
                        traceback.print_exc()

    # Show results
    if len(failed) == 0:
        print("All environment images built successfully.")
    else:
        print(f"{len(failed)} environment images failed to build.")

    return successful, failed


def main():
    parser = argparse.ArgumentParser(
        description="Build Singularity images for all registered repository profiles"
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=4,
        help="Maximum number of parallel workers (default: 4)",
    )
    parser.add_argument(
        "-r",
        "--repos",
        type=str,
        nargs="+",
        help="Repository name patterns to build (fuzzy match, space-separated)",
    )
    parser.add_argument(
        "--language",
        help="Restrict image builds to one profile language",
    )
    parser.add_argument(
        "-y", "--proceed", action="store_true", help="Proceed without confirmation"
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force rebuild even if image already exists",
    )
    parser.add_argument(
        "-p",
        "--publish",
        action="store_true",
        help="Run the compatibility publish step after building (default: False)",
    )
    parser.add_argument(
        "--list-envs", action="store_true", help="List all available profiles and exit"
    )
    parser.add_argument(
        "--arch",
        choices=["x86_64", "arm64"],
        help="Force build for specific architecture",
    )

    args = parser.parse_args()

    if args.list_envs:
        print("All execution environment Singularity images:")
        for profile in registry.values():
            print(f"  {profile.image_name}")
        return

    successful, failed = build_all_images(
        workers=args.workers,
        repo_filter=args.repos,
        language=args.language,
        proceed=args.proceed,
        publish=args.publish,
        force=args.force,
        arch=args.arch,
    )

    if failed:
        print(f"- Failed builds: {failed}")
    if successful:
        print(f"- Successful builds: {len(successful)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
