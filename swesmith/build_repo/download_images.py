"""Pull profile images from the OCI registry into the local Singularity cache.

Usage: uv run python -m swesmith.build_repo.download_images --repo <profile>
"""

import argparse

from swesmith.profiles import registry


def main(repo: str | None, proceed: bool = True):
    profiles = list(registry.values())
    if repo:
        value = repo.lower()
        profiles = [
            profile
            for profile in profiles
            if value in profile.image_name.lower()
            or value in profile.repo_name.lower()
            or value in f"{profile.owner}/{profile.repo}".lower()
        ]
    if not profiles:
        print(f"Could not find an environment for {repo}, exiting...")
        return
    print(f"Found {len(profiles)} environments:")
    for profile in profiles:
        print(f"- {profile.image_name} -> {profile.image_path}")
    if not proceed and input("Proceed with downloading images? (y/n): ").lower() != "y":
        return
    for profile in profiles:
        print(f"Downloading {profile.image_name}...")
        profile.download_image()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None)
    parser.add_argument("-y", "--proceed", action="store_true")
    args = parser.parse_args()
    main(args.repo, args.proceed)
