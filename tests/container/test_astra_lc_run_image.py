"""Built-image contract for the optional `lc` execution path.

The checkout-side contract lives in ``tests/unit/test_astra_lc_run_sbatch.py``.
What can only be checked here is that the template is installed and that the
two things it depends on -- `lc` and the `dask` CLI it shells out to for its
srun workers -- actually resolve on the PATH the template builds.
"""

from pathlib import Path

from testlib import run_cmd


TEMPLATE = Path("/opt/neurodesktop/astra_lc_run.sbatch")
TOOL_BIN = "/opt/uv/tools/lightcone-cli/bin"


def test_the_lc_sbatch_template_is_installed_and_readable():
    assert TEMPLATE.is_file()
    body = TEMPLATE.read_text(encoding="utf-8")
    assert "lc run" in body
    assert "status.json" in body


def test_lc_and_dask_both_resolve_on_the_path_the_template_builds():
    """The template's whole reason for touching PATH.

    `lc` shells out to the `dask` CLI to launch its srun workers, and both
    live in the isolated uv tool environment. If either stops resolving, every
    job submitted with this template dies inside its allocation.
    """
    for tool in ("lc", "dask", "astra"):
        code, output = run_cmd(
            f'PATH={TOOL_BIN}:$PATH command -v {tool}', timeout=60
        )
        assert code == 0, f"{tool} did not resolve: {output}"


def test_the_template_refuses_to_run_outside_an_allocation():
    """Run directly it must fail, not silently execute on the login node."""
    code, output = run_cmd(
        f"unset SLURM_JOB_ID; bash {TEMPLATE}", timeout=120
    )
    assert code != 0
    assert "sbatch" in output


def test_the_container_guard_can_import_its_yaml_parser():
    """The guard runs under /opt/conda/bin/python, not the lightcone env."""
    code, output = run_cmd(
        "/opt/conda/bin/python -c 'import yaml; print(yaml.__version__)'",
        timeout=60,
    )
    assert code == 0, output


def test_lc_status_json_still_has_the_shape_the_viewer_parses(tmp_path):
    """Run the real CLI against the shipped example and load the result.

    This is the join between the two halves: whatever `lc status --json`
    emits in this image has to come back out of the viewer's parser as
    per-output records, or the template writes a file that blanks the graph.
    """
    import json
    import shutil

    from neurodesk_astra_view.manifest import load_run

    project = tmp_path / "astra-bet"
    shutil.copytree("/opt/neurodesktop/examples/astra-bet", project)

    code, output = run_cmd(
        f"PATH={TOOL_BIN}:$PATH lc status --universe bet-f-0-5 --json",
        cwd=str(project),
        timeout=300,
    )
    assert code == 0, output

    status = project / "status.json"
    status.write_text(output[output.index("{") :], encoding="utf-8")
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["universes"], payload

    overlay = load_run(
        status, project_root=project, universe_id="bet-f-0-5"
    )
    assert overlay["records"], "no per-output records parsed from lc status"
    # Nothing has been materialized, so every output is honestly not_run and
    # the badge is amber rather than green.
    assert {r["status"] for r in overlay["records"].values()} == {"not_run"}
    assert overlay["trust"]["level"] == "executed-unverified"
