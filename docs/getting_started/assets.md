# Assets

In addition to the paper and codebase, we release the following assets created with SWE-smith:

1. **Environments for 128 GitHub repositories.** You can download the environments (Singularity images) locally by running the following command from the root directory of SWE-smith:
```bash
uv run python -m swesmith.build_repo.download_images
```

2. **SWE-smith dataset of 50k+ task instances**, made available as a [HuggingFace dataset](https://huggingface.co/datasets/SWE-bench/SWE-smith).
