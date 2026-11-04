import subprocess
import yaml
from testlib import repo_path


def test_build_cache_is_released_before_scanning(tmp_path):
    steps = yaml.safe_load(repo_path(".github/workflows/pr-image-validation.yml").read_text())["jobs"]["image"]["steps"]
    build = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("docker/build-push-action@"))
    scan = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("aquasecurity/trivy-action@"))
    cleanup = next(i for i, s in enumerate(steps) if s.get("name") == "Release build cache before image export")
    assert build < cleanup < scan
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\n[ "$*" = "buildx prune --all --force" ] || exit 99\n')
    docker.chmod(0o755)
    import os
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    subprocess.run(["bash", "-e", "-c", steps[cleanup]["run"]], env=env, check=True)
