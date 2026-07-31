import importlib.util
import json
import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from testlib import resolve_source


def _pilot_cli_path():
    return resolve_source(
        "/usr/local/bin/neurodesktop-astra-lightcone-pilot",
        "scripts/neurodesktop_astra_lightcone_pilot.py",
    )


def _run_pilot(*args, env=None):
    return subprocess.run(
        [sys.executable, str(_pilot_cli_path()), *map(str, args)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=env,
    )


def _load_pilot_module():
    path = _pilot_cli_path()
    spec = importlib.util.spec_from_file_location("neurodesktop_astra_lightcone_pilot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("state", "exit_code", "expected"),
    [
        ("COMPLETED", "0:0", "succeeded"),
        ("COMPLETED", "1:0", "failed"),
        ("FAILED", "7:0", "failed"),
        ("CANCELLED", "0:15", "cancelled"),
        ("TIMEOUT", "0:0", "timed-out"),
    ],
)
def test_terminal_outcome_never_promotes_a_non_success_state(
    state, exit_code, expected
):
    pilot = _load_pilot_module()

    assert pilot._terminal_outcome(state, exit_code) == expected


@pytest.mark.parametrize("value", [None, "", "not-a-number", "-1"])
def test_elapsed_seconds_rejects_malformed_slurm_accounting(value):
    pilot = _load_pilot_module()

    with pytest.raises(pilot.PilotError, match="invalid ElapsedRaw"):
        pilot._elapsed_seconds(value)


def test_terminal_control_wait_ignores_completing_and_returns_final_state(monkeypatch):
    pilot = _load_pilot_module()
    responses = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout="JobId=42 JobState=COMPLETING ExitCode=0:15\n",
            ),
            SimpleNamespace(
                returncode=0,
                stdout="JobId=42 JobState=CANCELLED ExitCode=0:15\n",
            ),
        ]
    )
    monkeypatch.setattr(pilot, "_run_external", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(pilot.time, "sleep", lambda _seconds: None)

    result = pilot._wait_for_terminal_scontrol("42", timeout_seconds=1)

    assert "JobState=CANCELLED" in result.stdout


def test_prepare_materializes_the_pinned_bounded_pilot_without_submitting(tmp_path):
    workspace = tmp_path / "workspace"

    result = _run_pilot(
        "prepare",
        "--workspace-root",
        workspace,
        "--project-only",
    )

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == f"prepared: {workspace}"

    contract = json.loads((workspace / "pilot-contract.json").read_text())
    assert contract["contractVersion"] == "1.0.0"
    assert contract["packagePins"] == {
        "astra-spec": "0.0.12",
        "astra-tools": "0.2.11",
        "lightcone-cli": "0.4.0",
    }
    assert contract["module"] == {
        "specifier": "fsl/6.0.7.22",
        "commands": ["bet", "fslstats", "slicer", "pngappend"],
    }
    assert contract["slurm"] == {
        "partition": "neurodesktop",
        "nodes": 1,
        "tasks": 1,
        "cpusPerTask": 1,
        "memoryMiB": 4096,
        "wallTimeSeconds": 900,
    }
    assert [universe["id"] for universe in contract["universes"]] == [
        "bet-f-0-5",
        "bet-f-0-3",
    ]
    assert [universe["fraction"] for universe in contract["universes"]] == [
        0.5,
        0.3,
    ]
    assert [output["id"] for output in contract["outputs"]] == [
        "bet_brain",
        "bet_mask",
        "mask_volume",
        "boundary_qc",
    ]

    source_manifest = json.loads(
        (workspace / "inputs" / "source-manifest.json").read_text()
    )
    assert source_manifest["snapshot"] == "ds000114/1.0.2"
    assert source_manifest["files"] == [
        {
            "relativePath": "openneuro/ds000114/1.0.2/dataset_description.json",
            "sha256": "5a4b7bd031bbf57fdab310b99d2327f7338e285baad42f953a59d9c9c4d5df33",
            "sizeBytes": 684,
            "url": (
                "https://raw.githubusercontent.com/OpenNeuroDatasets/"
                "ds000114/1.0.2/dataset_description.json"
            ),
        },
        {
            "relativePath": (
                "openneuro/ds000114/1.0.2/sub-01/ses-test/anat/"
                "sub-01_ses-test_T1w.nii.gz"
            ),
            "sha256": "2bbe4430223d9b9ce47d4dab3ff398a69fefbb481cbf3bd136e07f363a73919c",
            "sizeBytes": 8677710,
            "url": (
                "https://s3.amazonaws.com/openneuro.org/ds000114/sub-01/"
                "ses-test/anat/sub-01_ses-test_T1w.nii.gz"
            ),
        },
    ]

    astra_yaml = (workspace / "project" / "astra.yaml").read_text()
    assert 'version: "0.0.12"' in astra_yaml
    assert "container:" not in astra_yaml
    for output_id in ("bet_brain", "bet_mask", "mask_volume", "boundary_qc"):
        assert f"  - id: {output_id}\n" in astra_yaml
    assert (
        workspace / "project" / "universes" / "bet-f-0-5.yaml"
    ).is_file()
    assert (
        workspace / "project" / "universes" / "bet-f-0-3.yaml"
    ).is_file()

    sbatch = (workspace / "project" / "scripts" / "run-bet-pilot.sbatch").read_text()
    for required in (
        "#SBATCH --partition=neurodesktop",
        "#SBATCH --nodes=1",
        "#SBATCH --ntasks=1",
        "#SBATCH --cpus-per-task=1",
        "#SBATCH --mem=4096M",
        "#SBATCH --time=00:15:00",
        "source /opt/neurodesktop/environment_variables.sh",
        "module load fsl/6.0.7.22",
        'export PATH="/opt/uv/tools/lightcone-cli/bin:${PATH}"',
        "lc run --jobs 1",
        "lc status --json",
        "lc verify",
        "lc export wrroc",
        'cd "${PILOT_PROJECT_DIR}"',
    ):
        assert required in sbatch
    assert "apptainer" not in sbatch.lower()
    assert "singularity" not in sbatch.lower()
    assert not (workspace / "inputs" / "openneuro").exists()

    materializer = (
        workspace / "project" / "scripts" / "materialize-bet-output.sh"
    ).read_text()
    # Every FSL tool the recipes call must be guarded before use, and the
    # module's command wrappers must stay the sole tool-execution seam.
    guard = re.search(r"for command_name in ([^\n;]+); do", materializer)
    assert guard, materializer
    assert guard.group(1).split() == ["bet", "fslstats", "slicer", "pngappend"]
    assert 'command -v "${command_name}"' in materializer
    assert "apptainer" not in materializer.lower()
    assert "singularity" not in materializer.lower()

    evidence_collector = (
        workspace / "project" / "scripts" / "collect-pilot-evidence.py"
    ).read_text()
    assert "from lightcone.engine.verify import verify_outputs" in evidence_collector
    assert '".lightcone-manifest.json"' in evidence_collector


def test_prepare_fails_closed_before_download_for_a_modified_staged_input(tmp_path):
    workspace = tmp_path / "workspace"
    initial = _run_pilot(
        "prepare",
        "--workspace-root",
        workspace,
        "--project-only",
    )
    assert initial.returncode == 0, initial.stdout
    dataset_description = (
        workspace
        / "inputs"
        / "openneuro"
        / "ds000114"
        / "1.0.2"
        / "dataset_description.json"
    )
    dataset_description.parent.mkdir(parents=True)
    dataset_description.write_text("modified input\n")

    result = _run_pilot("prepare", "--workspace-root", workspace)

    assert result.returncode == 2
    assert "input dataset_description SHA-256 mismatch" in result.stdout
    assert dataset_description.read_text() == "modified input\n"
    assert not (
        workspace
        / "inputs"
        / "openneuro"
        / "ds000114"
        / "1.0.2"
        / "sub-01"
        / "ses-test"
        / "anat"
        / "sub-01_ses-test_T1w.nii.gz"
    ).exists()
    assert not list(workspace.rglob("*.tmp"))


def test_run_fails_before_submission_when_local_slurm_is_not_ready(tmp_path):
    workspace = tmp_path / "workspace"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "sbatch-called"
    scontrol = fake_bin / "scontrol"
    scontrol.write_text("#!/bin/sh\necho 'Slurmctld is DOWN'\nexit 1\n")
    scontrol.chmod(0o755)
    sbatch = fake_bin / "sbatch"
    sbatch.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    sbatch.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "NEURODESKTOP_SLURM_MODE": "local",
    }

    result = _run_pilot(
        "run",
        "--workspace-root",
        workspace,
        "--scheduler-timeout-seconds",
        "0",
        env=env,
    )

    assert result.returncode == 2
    assert "local Slurm scheduler was not ready within 0 seconds" in result.stdout
    assert "Slurmctld is DOWN" in result.stdout
    assert not marker.exists()
    assert not (workspace / "runs").exists()
