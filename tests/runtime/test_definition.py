from pathlib import Path

import pytest

from swesmith.runtime.definition import RecipeTranslationError, translate_recipe


def test_translate_recipe_preserves_environment_and_files(tmp_path: Path):
    (tmp_path / "setup.sh").write_text("#!/bin/sh\necho ready\n")
    definition = translate_recipe(
        """FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
WORKDIR /testbed
COPY setup.sh /setup.sh
RUN bash /setup.sh
CMD [\"/bin/bash\"]
""",
        context_dir=tmp_path,
    )

    assert "Bootstrap: docker\nFrom: ubuntu:22.04" in definition
    assert "export DEBIAN_FRONTEND=noninteractive" in definition
    assert "export LANG=C.UTF-8" in definition
    assert "setup.sh /setup.sh" in definition
    assert "cd /testbed && bash /setup.sh" in definition
    assert "%runscript" in definition
    assert 'exec /bin/bash "$@"' in definition


def test_translate_recipe_rejects_unsupported_instruction():
    with pytest.raises(RecipeTranslationError, match="EXPOSE"):
        translate_recipe("FROM ubuntu:22.04\nEXPOSE 8000\n")
