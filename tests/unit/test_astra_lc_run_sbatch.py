"""Contract for the optional `lc` execution path.

`config/slurm/astra_lc_run.sbatch` is the only supported way to get run
evidence out of Neurodesktop. `lc run` never submits to Slurm itself -- it
dispatches through Dask and, inside an allocation, launches workers with
srun -- so the template exists to put `lc` *inside* an sbatch job rather than
in front of one.

The default flow is still one plain sbatch script per step, which produces no
manifest at all; see `config/agents/AGENTS.md`.
"""

import json
import re
import subprocess
import sys

import pytest

from testlib import repo_path


TEMPLATE = repo_path("config/slurm/astra_lc_run.sbatch")
SOURCE = TEMPLATE.read_text(encoding="utf-8")

sys.path.insert(0, str(repo_path("extensions/astra-viewer")))


def test_the_template_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(TEMPLATE)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_the_template_ships_in_the_image():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert (
        "install -m 0644 /tmp/slurm/astra_lc_run.sbatch "
        "/opt/neurodesktop/astra_lc_run.sbatch" in dockerfile
    )


def test_it_refuses_to_run_outside_an_allocation():
    """`lc run` only reaches its srun path when SLURM_JOB_ID is set."""
    assert ': "${SLURM_JOB_ID:?' in SOURCE


def test_the_lightcone_tool_bin_goes_on_path():
    """`lc` shells out to CLIs it does not import, and they must resolve.

    `dask` starts the srun workers and `snakemake` drives the rules; a
    missing one surfaces as a bare FileNotFoundError traceback from deep
    inside lc, which is the single most likely way this template breaks.
    """
    assert 'export PATH="/opt/uv/tools/lightcone-cli/bin:${PATH}"' in SOURCE
    assert "for required in lc dask snakemake astra; do" in SOURCE


def test_modules_must_carry_an_explicit_version():
    assert "Refusing to load" in SOURCE
    assert "module purge" in SOURCE


def test_status_json_is_renamed_into_place_not_streamed():
    """A half-written manifest is evidence the viewer refuses, and refusing
    takes the whole graph down rather than just the run overlay."""
    assert "> status.json.tmp" in SOURCE
    assert "mv status.json.tmp status.json" in SOURCE


def test_it_refuses_to_create_an_ambiguous_run_directory():
    """Two recognised manifests beside one spec fail closed in manifest.py."""
    for name in ("run-manifest.json", "manifest.json", "ro-crate-metadata.json"):
        assert name in SOURCE, name
    assert "Refusing to write status.json" in SOURCE


# ---------------------------------------------------------------------------
# The container guard
#
# Apptainer is not one of lc's runtimes, so a declared image would be recorded
# as used without ever running -- the provenance mismatch the viewer paints
# red. The template refuses rather than recording it.


def container_guard() -> str:
    match = re.search(
        r"^/opt/conda/bin/python - .*?<<'PY'\n(.*?)\nPY$", SOURCE, re.M | re.S
    )
    assert match, "container guard heredoc not found in the template"
    return match.group(1)


def run_guard(tmp_path, spec: str):
    script = tmp_path / "guard.py"
    script.write_text(container_guard(), encoding="utf-8")
    target = tmp_path / "astra.yaml"
    target.write_text(spec, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script), str(target)],
        capture_output=True,
        text=True,
    )


def test_guard_allows_a_module_load_spec(tmp_path):
    pytest.importorskip("yaml")

    result = run_guard(
        tmp_path,
        "id: a\noutputs:\n  - id: o\n    type: data\n"
        "    recipe: {command: 'bash src/run.sh'}\n",
    )
    assert result.returncode == 0, result.stderr


def test_guard_allows_the_canonical_worked_example(tmp_path):
    pytest.importorskip("yaml")

    spec = repo_path("tests/fixtures/astra-bet/astra.yaml").read_text(
        encoding="utf-8"
    )
    assert run_guard(tmp_path, spec).returncode == 0


@pytest.mark.parametrize(
    "spec, where",
    [
        ("id: a\ncontainer: python:3.12-slim\n", "<root>"),
        (
            "id: a\noutputs:\n  - id: o\n    type: data\n"
            "    recipe: {command: x, container: python:3.12-slim}\n",
            "o",
        ),
        (
            "id: a\nanalyses:\n  sub:\n    id: sub\n"
            "    container: python:3.12-slim\n",
            "sub",
        ),
    ],
)
def test_guard_refuses_a_declared_container(tmp_path, spec, where):
    pytest.importorskip("yaml")

    result = run_guard(tmp_path, spec)
    assert result.returncode != 0
    assert where in result.stderr
    assert "module load" in result.stderr


# ---------------------------------------------------------------------------
# What `lc status --json` actually produces
#
# Recorded from lightcone-cli 0.4.0 against tests/fixtures/astra-bet. The
# viewer parses this shape directly, so a drift upstream shows up here rather
# than as a blank graph.


LC_STATUS_JSON = {
    "universes": [
        {
            "universe_id": "bet-f-0-5",
            "outputs": [
                {
                    "output_id": "bet_brain",
                    "analysis_id": None,
                    "status": "ok",
                    "recipe_command": "bash src/materialize-bet-output.sh bet_brain",
                },
                {
                    "output_id": "boundary_qc",
                    "analysis_id": None,
                    "status": "missing",
                    "recipe_command": "bash src/materialize-bet-output.sh boundary_qc",
                },
            ],
        },
        {
            "universe_id": "bet-f-0-3",
            "outputs": [
                {
                    "output_id": "bet_brain",
                    "analysis_id": None,
                    "status": "stale",
                    "recipe_command": "bash src/materialize-bet-output.sh bet_brain",
                }
            ],
        },
    ]
}


def test_lc_status_json_loads_as_run_evidence(tmp_path):
    """The template's output has to be readable by the viewer, unmodified."""
    from neurodesk_astra_view.manifest import load_run

    (tmp_path / "status.json").write_text(
        json.dumps(LC_STATUS_JSON), encoding="utf-8"
    )

    overlay = load_run(
        tmp_path / "status.json", project_root=tmp_path, universe_id="bet-f-0-5"
    )

    # Only the selected universe's records survive.
    assert set(overlay["records"]) == {"bet_brain", "boundary_qc"}
    assert overlay["records"]["bet_brain"]["status"] == "ok"
    assert overlay["records"]["bet_brain"]["command"].startswith("bash src/")


def test_an_unmaterialized_output_reads_as_not_run(tmp_path):
    """`lc` spells it "missing"; "unknown" would claim we could not tell."""
    from neurodesk_astra_view.manifest import load_run

    (tmp_path / "status.json").write_text(
        json.dumps(LC_STATUS_JSON), encoding="utf-8"
    )

    overlay = load_run(
        tmp_path / "status.json", project_root=tmp_path, universe_id="bet-f-0-5"
    )
    assert overlay["records"]["boundary_qc"]["status"] == "not_run"


def test_lc_evidence_is_amber_and_never_green(tmp_path):
    """`lc verify` prints to the console and stamps nothing into the manifest.

    Neurodesktop does not synthesize the verification record it did not earn,
    so this path tops out at amber by construction.
    """
    from neurodesk_astra_view.manifest import load_run

    (tmp_path / "status.json").write_text(
        json.dumps(LC_STATUS_JSON), encoding="utf-8"
    )

    trust = load_run(
        tmp_path / "status.json", project_root=tmp_path, universe_id="bet-f-0-5"
    )["trust"]

    assert trust["level"] == "executed-unverified"
    assert trust["label"] == "Executed, unverified"
    assert trust["output_integrity"] == "not-run"


def test_a_spec_only_project_stays_grey_without_the_template(tmp_path):
    """The default plain-sbatch flow writes no manifest, and that is honest."""
    from neurodesk_astra_view.manifest import spec_only_trust

    assert spec_only_trust()["level"] == "spec-only"
    assert spec_only_trust()["label"] == "Not executed"
