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
