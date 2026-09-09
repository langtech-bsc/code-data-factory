#!/bin/bash

# This script comes in handy when you want to test building an image 
# for a specific NON-PYTHON repo when one builds the profile for it.

# TODO: Fill in the Github token
# export GITHUB_TOKEN=""

# TODO: Fill in the image name
image_name="richardzhuang0412/swesmith.x86_64.burntsushi_1776_rust-csv.da000888"

# TODO: Fill in the test command
test_cmd="cargo test --verbose"

# NOTE: The image name should be of the form:
#   <github_username>/swesmith.x86_64.<repo_owner>.<repo_name>.<commit_hash>
# The original script had an incorrect image name format (with _1776_ in it).

# Build the image
uv run python -m swesmith.build_repo.create_images --repos "$image_name" -y

# Test the Singularity image by running the test command
sif_root="${SWESMITH_SIF_ROOT:-/gpfs/scratch/bsc88/post-training/singularities/code-data-factory}"
sif_path="$sif_root/$(printf '%s' "$image_name" | sed 's#^.*/##; s/:/_/g').sif"
singularity exec "$sif_path" bash -lc "$test_cmd"
