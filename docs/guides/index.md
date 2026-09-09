# Tutorials

Build executable task data for software engineering agents. This guide covers the workflow from environment setup through validation and issue generation.

---

## 📋 Prerequisites

!!! info "System Requirements"
    **Required:** Singularity/Apptainer and Slurm on MN5
    **Tested on:** Ubuntu 22.04.4 LTS
    **Not supported:** Windows, macOS

New to SWE-smith? Start with [Installation](../getting_started/installation.md) and [Quickstart](../getting_started/quickstart.md).

---

## 🎯 Quick Navigation

<div class="grid cards" markdown>

-   :material-package-variant: **Build Environments**

    ---

    Create reproducible Singularity images for any repository. Capture dependencies, build images, and validate with automated testing.

    [:octicons-arrow-right-24: Get started](env_construction.md)

-   :material-bug: **Create Instances**

    ---

    Generate task instances using LM prompts, procedural modifications, PR mirroring, or combined techniques. Scale to thousands of bugs.

    [:octicons-arrow-right-24: Generate bugs](create_instances.md)

-   :material-check-circle: **Validate & Evaluate**

    ---

    Filter candidates that break tests and verify proposed solutions. Built-in harnesses for validation and evaluation workflows.

    [:octicons-arrow-right-24: Run harnesses](harnesses.md)

-   :material-file-document: **Generate Issue Text**

    ---

    Add natural language problem statements to task instances using LM generation or alternative methods.

    [:octicons-arrow-right-24: Create issues](issue_gen.md)

</div>

---

##  Recommended Workflow

```mermaid
graph LR
    A[Build Environments] --> B[Create Instances]
    B --> C[Validate & Evaluate]
    C --> D[Generate Issue Text]
```

1. **Build Environments** → Set up Singularity images
2. **Create Instances** → Generate synthetic bugs
3. **Validate & Evaluate** → Filter valid task instances
4. **Generate Issue Text** → Add problem descriptions
