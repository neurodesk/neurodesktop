"""Execute the default-permission Slurm image check and its cleanup paths."""

import json
import os
import subprocess
import sys

import pytest
import yaml

from testlib import repo_path


@pytest.mark.parametrize("failure", ["", "run", "bootstrap", "tests", "cleanup"])
def test_slurm_image_validation_preserves_permissions_and_cleans_up(tmp_path, failure):
    commands = tmp_path / "bin"
    commands.mkdir()
    trace = tmp_path / "docker.jsonl"
    docker = commands / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["DOCKER_TRACE"], "a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[0] == "run":
    stage = "run"
elif args[0] == "rm":
    stage = "cleanup"
elif args[-1] == "/opt/neurodesktop/setup_and_start_slurm.sh":
    stage = "bootstrap"
else:
    stage = "tests"
sys.exit(37 if stage == os.environ["FAIL_STAGE"] else 0)
''')
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(repo_path("scripts/verify_slurm_image.sh")), "candidate:slurm"],
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}",
             "DOCKER_TRACE": str(trace), "FAIL_STAGE": failure},
        capture_output=True, text=True, timeout=10,
    )
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    run = calls[0]
    name = run[run.index("--name") + 1]
    assert name.startswith("neurodesktop-slurm-test-")
    assert run == ["run", "-d", "--init", "--user", "root", "--name", name,
                   "-e", "NEURODESKTOP_SLURM_ENABLE=1", "-e", "NEURODESKTOP_SLURM_MODE=local",
                   "--entrypoint", "/bin/bash", "candidate:slurm", "-c", "sleep infinity"]
    assert calls[-1] == ["rm", "-f", name]
    if failure != "run":
        assert calls[1] == ["exec", name, "/opt/neurodesktop/setup_and_start_slurm.sh"]
    if failure not in ("run", "bootstrap"):
        assert calls[2] == ["exec", "--user", "jovyan", name,
                            "pytest", "/opt/tests/test_slurm.py", "-v"]
    assert result.returncode == (1 if failure == "cleanup" else 37 if failure else 0)


def test_pr_candidates_run_default_permission_slurm_check():
    workflow = yaml.safe_load(repo_path(".github/workflows/pr-image-validation.yml").read_text())
    steps = workflow["jobs"]["image"]["steps"]
    assert any(step.get("run") == 'exec bash scripts/verify_slurm_image.sh "$IMAGE_REF"'
               for step in steps)
