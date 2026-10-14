import os
import subprocess
import sys

import pytest
import yaml

from testlib import repo_path


def test_pull_requests_build_and_test_native_candidates_without_publishing():
    workflow = yaml.safe_load(repo_path(".github/workflows/pr-image-validation.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" in triggers
    assert "pull_request_target" not in triggers
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["image"]
    assert job["strategy"]["matrix"]["include"] == [
        {"arch": "amd64", "runner": "ubuntu-24.04"},
        {"arch": "arm64", "runner": "ubuntu-24.04-arm"},
    ]
    steps = job["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action@"))
    assert build["with"]["load"] is True
    assert build["with"]["push"] is False
    assert build["with"]["tags"] == "${{ env.IMAGE_REF }}"
    assert "github.run_id" in job["env"]["IMAGE_REF"]
    assert "github.run_attempt" in job["env"]["IMAGE_REF"]
    assert "matrix.arch" in job["env"]["IMAGE_REF"]
    runtime = [step for step in steps if "validate_image_runtime.sh" in step.get("run", "")]
    assert len(runtime) == 3
    assert all('"$IMAGE_REF"' in step["run"] for step in runtime)
    assert not runtime[0].get("if") and "regular true packages false" in runtime[0]["run"]
    assert not runtime[1].get("if") and " hpc" in runtime[1]["run"]
    assert runtime[2]["if"] == "matrix.arch == 'amd64'"
    assert " acceptance" in runtime[2]["run"]
    source = repo_path(".github/workflows/pr-image-validation.yml").read_text()
    assert "secrets." not in source
    assert "docker/login" not in source
    assert "self-hosted" not in source


@pytest.mark.parametrize("original_policy", ["1", "0"])
@pytest.mark.parametrize("failure", ["", "read", "write"])
def test_runtime_host_policy_precedes_regular_checks_and_is_restored(tmp_path, original_policy, failure):
    steps = yaml.safe_load(repo_path(".github/workflows/pr-image-validation.yml").read_text())["jobs"]["image"]["steps"]
    regular = next(index for index, step in enumerate(steps)
                   if step.get("run", "").endswith(" regular true packages false"))
    before = [step for step in steps[:regular]
              if "apparmor_restrict_unprivileged_userns" in step.get("run", "")]
    after = [step for step in steps[regular + 1:]
             if "apparmor_restrict_unprivileged_userns" in step.get("run", "")]
    assert len(before) == 1, "Both architectures need namespaces before the regular suite"
    assert len(after) == 1, "Both architectures must restore the captured host policy"
    commands = tmp_path / "bin"
    commands.mkdir()
    policy = tmp_path / "policy"
    policy.write_text(original_policy)
    sysctl = commands / "sysctl"
    sysctl.write_text(f"#!{sys.executable}\n" + """
import os
from pathlib import Path
import sys
state = Path(os.environ["TEST_HOST_POLICY"])
failure = os.environ["TEST_POLICY_FAILURE"]
if failure == "read" and sys.argv[1] == "-n":
    sys.exit(42)
if failure == "write" and sys.argv[1:] == ["-w", "kernel.apparmor_restrict_unprivileged_userns=0"]:
    sys.exit(42)
if sys.argv[1:] == ["-n", "kernel.apparmor_restrict_unprivileged_userns"]:
    print(state.read_text())
elif sys.argv[1] == "-w" and sys.argv[2].startswith("kernel.apparmor_restrict_unprivileged_userns="):
    value = sys.argv[2].split("=", 1)[1]
    assert value in {"0", "1"}
    state.write_text(value)
else:
    raise AssertionError(sys.argv)
""")
    sysctl.chmod(0o755)
    sudo = commands / "sudo"
    sudo.write_text('#!/bin/sh\nexec "$@"\n')
    sudo.chmod(0o755)
    env = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"],
           "RUNNER_TEMP": str(tmp_path), "TEST_HOST_POLICY": str(policy),
           "TEST_POLICY_FAILURE": failure}
    for step in before:
        assert not step.get("if"), "The prerequisite must run on both architectures"
        result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, check=False)
        assert result.returncode == (42 if failure else 0)
    assert policy.read_text() == (original_policy if failure else "0")
    env["TEST_POLICY_FAILURE"] = ""
    for step in after:
        assert step["if"] == "always()", "Restore on both architectures, including failed runs"
        subprocess.run(["bash", "-e", "-c", step["run"]], env=env, check=True)
    assert policy.read_text() == original_policy
