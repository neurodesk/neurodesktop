import json
import os
from pathlib import Path
import subprocess
import time

import pytest

LC = "/opt/uv/tools/lightcone-cli/bin/lc"
CATALOG = "/opt/neurodesktop/lightcone-compute.yaml"


def command(root, *args, env=None, timeout=360):
    result = subprocess.run(
        args,
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result.stdout


def commit(root):
    command(root, "git", "add", "-A")
    command(root, "git", "commit", "-qm", "Define execution case")


def wait_ended(root, cluster, env):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        snapshot = json.loads(
            command(root, LC, "compute", "status", cluster, "--json", env=env)
        )
        if snapshot["phase"] == "ended":
            return
        time.sleep(1)
    pytest.fail(f"owned allocation did not reach ended state: {snapshot}")


def project(tmp_path, recipe, *, modules=False, fmt="txt"):
    root = tmp_path / "analysis"
    root.mkdir()
    command(root, "git", "init", "-q")
    command(root, "git", "config", "user.name", "Neurodesk image acceptance")
    command(root, "git", "config", "user.email", "image-test@example.invalid")
    command(root, LC, "init", ".", "--json")
    path = root / "pyproject.toml"
    path.write_text(
        path.read_text().replace("[project]", '[project]\nlicense = "MIT"')
        + (
            '\n[tool.neurodesk.lightcone]\nexecution = "modules"\nmodules = ["fsl/6.0.7.22"]\n'
            if modules
            else ""
        )
    )
    (root / "astra.yaml").write_text(
        'version: "0.0.14"\nname: "Image execution"\ninputs: []\noutputs:\n'
        f"  - id: answer\n    type: data\n    format: {fmt}\n    recipe:\n"
        f'      command: {json.dumps(recipe)}\n      resources: {{cpus: 1, memory: "1Gi"}}\n'
    )
    commit(root)
    return root


def test_lc05_installed_native_contract():
    assert command(Path.cwd(), LC, "--version").strip() == "lc, version 0.5.0"
    assert Path("/usr/local/bin/neurodesk-lc-materialize").is_file()
    assert not Path("/opt/neurodesktop/astra_lc_run.sbatch").exists()
    command(
        Path.cwd(),
        LC,
        "compute",
        "launch",
        "--cpus",
        "2",
        "--memory",
        "8",
        "--dry-run",
        "--json",
        env={**os.environ, "LC_COMPUTE_CONFIG": CATALOG},
    )


def test_native_slurm_materializes_and_rebuilds(tmp_path):
    from neurodesk_astra_view.graph import build_graph

    if os.environ.get("NEURODESKTOP_SLURM_ENABLE", "1") in {"0", "false", "no"}:
        pytest.skip("Slurm is explicitly disabled")
    command(Path.cwd(), "scontrol", "ping")
    root = project(
        tmp_path,
        "python -c \"from pathlib import Path; Path('{output}').write_text('42')\"",
    )
    env = {**os.environ, "LC_COMPUTE_CONFIG": CATALOG}
    launched = json.loads(
        command(
            root,
            LC,
            "compute",
            "launch",
            "--cpus",
            "2",
            "--memory",
            "8",
            "--json",
            env=env,
        )
    )
    cluster = launched["id"]
    try:
        command(root, LC, "compute", "status", cluster, "--wait", "--json", env=env)
        command(
            root, LC, "run", cluster, "--", "python", "-c", "print('ready')", env=env
        )
        command(root, LC, "materialize", cluster, "baseline/answer", env=env)
        assert (root / "results/baseline/answer.txt").read_text() == "42"
        manifest = root / "results/baseline/.answer.manifest.json"
        before = manifest.read_bytes()
        assert "modules" not in json.loads(before)["hermeticity"]
        command(root, LC, "materialize", cluster, "baseline/answer", env=env)
        assert manifest.read_bytes() == before
        command(root, LC, "materialize", "--check", "baseline/answer", env=env)
        graph = build_graph(
            root / "astra.yaml",
            root / "universes/baseline.yaml",
            root / "ro-crate-metadata.json",
        )
        assert not graph["errors"]
        assert graph["trust"]["level"] == "executed-unverified"
        spec = root / "astra.yaml"
        spec.write_text(spec.read_text().replace("42", "43"))
        commit(root)
        status = json.loads(command(root, LC, "status", "--json", env=env))
        assert status["outputs"][0]["status"] == "stale"
        command(root, LC, "materialize", cluster, "baseline/answer", env=env)
        assert (root / "results/baseline/answer.txt").read_text() == "43"
    finally:
        command(root, LC, "compute", "down", cluster, "--json", env=env)
        wait_ended(root, cluster, env)


@pytest.mark.skipif(
    os.environ.get("NEURODESKTOP_TEST_LIGHTCONE_SCIENCE") != "1",
    reason="Enable the native Slurm/CVMFS/Apptainer science gate explicitly",
)
def test_fsl_module_materializes_through_real_worker(tmp_path):
    import nibabel as nib
    import numpy as np
    from neurodesk_astra_view.graph import build_graph

    command(Path.cwd(), "scontrol", "ping")
    assert Path("/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules").is_dir()
    root = project(tmp_path, "bash src/double.sh {output}", modules=True, fmt="nii.gz")
    (root / "src").mkdir()
    (root / "src/double.sh").write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        'case "$(command -v fslmaths)" in /cvmfs/*) ;; *) exit 1 ;; esac\n'
        'fslmaths data/input.nii.gz -mul 2 "$1"\n'
    )
    data = np.arange(1, 61, dtype=np.float32).reshape(3, 4, 5)
    nib.save(nib.Nifti1Image(data, np.eye(4)), root / "data/input.nii.gz")
    spec = root / "astra.yaml"
    spec.write_text(
        spec.read_text()
        .replace(
            "inputs: []",
            "inputs:\n  - id: image\n    type: data\n    source: data/input.nii.gz",
        )
        .replace(
            "    type: data\n    format:",
            "    type: data\n    inputs: [image]\n    format:",
        )
    )
    commit(root)
    env = {**os.environ, "LC_COMPUTE_CONFIG": CATALOG}
    transcript = command(
        root, "neurodesk-lc-materialize", "baseline/answer", env=env, timeout=900
    )
    events = [
        json.loads(line) for line in transcript.splitlines() if line.startswith("{")
    ]
    assert events[-1]["termination_requested"] is True
    assert events[0]["id"] == events[-1]["id"]
    wait_ended(root, events[-1]["id"], env)
    output = root / "results/baseline/answer.nii.gz"
    np.testing.assert_array_equal(nib.load(output).get_fdata(), data * 2)
    manifest = json.loads((root / "results/baseline/.answer.manifest.json").read_text())
    assert manifest["hermeticity"]["fs"] == "open"
    assert manifest["hermeticity"]["mechanism"] == "none"
    assert manifest["hermeticity"]["modules"] == ["fsl/6.0.7.22"]
    graph = build_graph(
        root / "astra.yaml",
        root / "universes/baseline.yaml",
        root / "ro-crate-metadata.json",
    )
    assert not graph["errors"]
    assert graph["trust"]["level"] == "executed-unverified"
    assert not (root / "status.json").exists()
