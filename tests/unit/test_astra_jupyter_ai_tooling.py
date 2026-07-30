"""Build-order and exclusion contracts for the ASTRA and Jupyter AI layers.

This tier only asserts what a built image cannot answer for itself: layer
ordering, deliberate exclusions, and the absence of a duplicate install. Every
version pin is verified against the *installed* package in
``tests/container/test_astra_agent_skills_image.py`` and
``test_astra_jupyter_ai_image.py``, so it is not re-asserted as a Dockerfile
substring here -- that would only add a second place to edit on every bump.
"""

from testlib import repo_path


DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")


def test_jq_is_installed_for_the_astra_agent_hooks():
    """Every ASTRA plugin hook parses its payload with jq."""
    assert "\n    jq \\\n" in DOCKERFILE


def test_astra_tools_is_installed_exactly_once():
    """A second isolated copy would shadow the one the viewer imports."""
    assert 'uv tool install "astra-tools' not in DOCKERFILE
    assert 'test "$(command -v astra)" = "/opt/conda/bin/astra"' in DOCKERFILE
    # The conda install plus the deliberate `--with` into the isolated
    # lightcone-cli tool environment; nothing else.
    assert DOCKERFILE.count("astra-tools==${ASTRA_TOOLS_VERSION}") == 2


def test_astra_version_arguments_are_declared_once_per_stage():
    """Two defaults for one pin is a second place to forget on a bump."""
    for argument in ("ASTRA_SPEC_VERSION", "ASTRA_TOOLS_VERSION"):
        assert DOCKERFILE.count(f"ARG {argument}=") == 1, argument


def test_the_astra_skill_reaches_all_three_agents():
    assert "codex plugin add astra@lightcone-research" in DOCKERFILE
    assert "claude plugin install astra@lightcone-research" in DOCKERFILE
    # OpenCode has no marketplace client; it reads SKILL.md from HOME.
    assert "/opt/jovyan_defaults/.config/opencode/skills/astra" in DOCKERFILE


def test_only_the_astra_plugin_is_installed_by_default():
    """`reproduction` drives long autonomous loops; it stays opt-in."""
    assert "reproduction@lightcone-research" not in DOCKERFILE


def test_upstream_checkouts_are_pinned_to_an_exact_commit():
    """A moving branch would make the image unreproducible.

    Each reference is asserted only once its ARG exists, so this holds while
    the checkouts are still being added rather than only at the end.
    """
    references = [
        "AGENT_SKILLS_REF",
        "JUPYTER_COLLABORATION_REF",
        "MYSTRA_REF",
        "ASTRA_THEME_REF",
    ]
    present = [name for name in references if f"ARG {name}=" in DOCKERFILE]
    assert present, "no pinned upstream checkout found"

    for reference in present:
        assert f'rev-parse HEAD)" = "${{{reference}}}"' in DOCKERFILE, reference
