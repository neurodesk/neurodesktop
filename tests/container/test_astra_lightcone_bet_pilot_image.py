import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from testlib import resolve_source, run_cmd


def test_astra_lightcone_pilot_cli_assets_and_exact_runtime_are_installed():
    cli = resolve_source(
        "/usr/local/bin/neurodesktop-astra-lightcone-pilot",
        "scripts/neurodesktop_astra_lightcone_pilot.py",
    )
    project = resolve_source(
        "/opt/neurodesktop/pilots/astra-lightcone-bet/project/astra.yaml",
        "pilots/astra-lightcone-bet/project/astra.yaml",
    ).parent

    code, output = run_cmd(f"{sys.executable} {cli} --help")
    assert code == 0, output
    assert "{prepare,run,verify}" in output

    lightcone_python = Path("/opt/uv/tools/lightcone-cli/bin/python")
    assert lightcone_python.is_file()
    code, output = run_cmd(
        f"{lightcone_python} -c \"import importlib.metadata as m; "
        "print(m.version('lightcone-cli'), m.version('astra-tools'), "
        "m.version('astra-spec'))\""
    )
    assert code == 0, output
    assert output == "0.4.0 0.2.11 0.0.12"

    code, output = run_cmd("astra validate astra.yaml", cwd=project)
    assert code == 0, output
    assert "Schema validation passed" in output
    assert (project / "src" / "run-bet-pilot.sbatch").is_file()
    assert (project / "src" / "materialize-bet-output.sh").is_file()


@pytest.mark.skipif(
    os.environ.get("NEURODESKTOP_RUN_ASTRA_LIGHTCONE_PILOT") != "1",
    reason="the real module/Slurm pilot is an explicit image acceptance test",
)
def test_real_local_slurm_module_pilot_and_fresh_verification():
    cli = Path("/usr/local/bin/neurodesktop-astra-lightcone-pilot")
    with tempfile.TemporaryDirectory(
        prefix="astra-lightcone-pilot-", dir="/neurodesktop-storage"
    ) as temporary_directory:
        workspace = Path(temporary_directory)
        code, output = run_cmd(
            f"{cli} run --workspace-root {workspace}",
            env={"NEURODESKTOP_SLURM_MODE": "local"},
            timeout=1200,
        )
        assert code == 0, output
        receipt = next((workspace / "runs").glob("*/receipt/receipt.json"))
        assert receipt.is_file()
        receipt_payload = json.loads(receipt.read_text())
        assert receipt_payload["outcome"] == "succeeded"
        assert receipt_payload["trust"] == {
            "level": "executed-unverified",
            "outputIntegrity": "passed",
            "actualContainerIdentity": None,
            "actualContainerIdentityAttested": False,
            "statement": (
                "The actual tool-container identity is not attested by this receipt."
            ),
        }
        assert len(receipt_payload["outputs"]) == 8
        assert receipt_payload["roCrate"] is not None
        for manifest in receipt_payload["lightcone"]["manifests"]:
            manifest_path = workspace / manifest["artifact"]["relativePath"]
            manifest_payload = json.loads(manifest_path.read_text())
            assert manifest_payload["slurm_job_id"] == receipt_payload["slurm"][
                "jobId"
            ]

        code, output = run_cmd(
            f"{cli} verify --run-directory {receipt.parents[1]}",
            timeout=180,
        )
        assert code == 0, output
        assert output.startswith("verified:")

        result_path = next(
            (receipt.parents[1] / "project" / "results").glob(
                "*/mask_volume/*_mask-metrics.json"
            )
        )
        original = result_path.read_bytes()
        result_path.write_bytes(original + b"\n")
        code, output = run_cmd(
            f"{cli} verify --run-directory {receipt.parents[1]}",
            timeout=180,
        )
        assert code != 0
        assert "fresh Lightcone verification failed" in output

        result_path.write_bytes(original)
        code, output = run_cmd(
            f"{cli} verify --run-directory {receipt.parents[1]}",
            timeout=180,
        )
        assert code == 0, output
        assert output.startswith("verified:")
