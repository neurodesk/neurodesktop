from pathlib import Path

import pytest


BUILD_WORKFLOWS = (
    "build-neurodesktop.yml",
    "build-neurodesktop-test.yml",
    "build-neurodesktop-dev.yml",
)


def _repo_root():
    root = Path(__file__).resolve().parents[1]
    if not (root / ".github").is_dir():
        pytest.skip(".github workflow sources are not installed in this test environment")
    return root


def _read_repo_file(relative_path):
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


def _step_bodies(workflow_text, step_name):
    lines = workflow_text.splitlines()
    needle = f"- name: {step_name}"
    for index, line in enumerate(lines):
        if line.strip() != needle:
            continue
        indent = len(line) - len(line.lstrip())
        body = []
        for next_line in lines[index + 1 :]:
            next_indent = len(next_line) - len(next_line.lstrip())
            if next_indent == indent and next_line.strip().startswith("- name: "):
                break
            body.append(next_line)
        yield "\n".join(body)


def _local_action_uses(workflow_text):
    in_jobs = False
    current_job = None
    checkout_seen = False

    for line_number, line in enumerate(workflow_text.splitlines(), start=1):
        if line == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue

        stripped = line.strip()
        if line.startswith("  ") and not line.startswith("    ") and stripped.endswith(":"):
            current_job = stripped[:-1]
            checkout_seen = False
            continue

        if "uses: actions/checkout@" in line:
            checkout_seen = True
        if "uses: ./.github/actions/" in line:
            yield current_job, line_number, stripped, checkout_seen


def test_ghcr_login_steps_use_retry_action():
    for workflow_name in BUILD_WORKFLOWS:
        workflow_text = _read_repo_file(f".github/workflows/{workflow_name}")
        ghcr_login_steps = list(_step_bodies(workflow_text, "Login to GHCR"))

        assert ghcr_login_steps, f"{workflow_name} has no GHCR login steps"
        for step_body in ghcr_login_steps:
            assert "uses: ./.github/actions/docker-login-retry" in step_body
            assert "registry: ghcr.io" in step_body
            assert "docker/login-action@" not in step_body


def test_production_manifest_checks_use_retry_action():
    workflow_text = _read_repo_file(".github/workflows/build-neurodesktop.yml")
    check_steps = list(_step_bodies(workflow_text, "Check if image exists"))

    assert len(check_steps) == 2
    for step_body in check_steps:
        assert "uses: ./.github/actions/check-registry-manifest" in step_body
        assert "image: ${{ env.IMAGEID }}:${{ env.BUILDDATE }}" in step_body

    assert "docker manifest inspect $IMAGEID:$BUILDDATE" not in workflow_text


def test_production_qemu_setup_only_runs_for_emulated_arch():
    workflow_text = _read_repo_file(".github/workflows/build-neurodesktop.yml")
    qemu_steps = list(_step_bodies(workflow_text, "Set up QEMU"))

    assert len(qemu_steps) == 1
    step_body = qemu_steps[0]
    assert "matrix.platform.arch == 'arm64'" in step_body
    assert "platforms: arm64" in step_body


def test_local_actions_run_after_checkout():
    for workflow_name in BUILD_WORKFLOWS:
        workflow_text = _read_repo_file(f".github/workflows/{workflow_name}")
        local_action_uses = list(_local_action_uses(workflow_text))

        assert local_action_uses, f"{workflow_name} has no local action uses"
        for job_name, line_number, action_use, checkout_seen in local_action_uses:
            assert checkout_seen, (
                f"{workflow_name}:{line_number} uses {action_use} in job "
                f"{job_name} before actions/checkout has populated local actions"
            )


def test_retry_actions_bound_transient_registry_failures():
    login_action = _read_repo_file(".github/actions/docker-login-retry/action.yml")
    manifest_action = _read_repo_file(".github/actions/check-registry-manifest/action.yml")

    assert 'default: "5"' in login_action
    assert "Registry probe ${REGISTRY}" in login_action
    assert 'timeout "${ATTEMPT_TIMEOUT_SECONDS}s" bash -c' in login_action
    assert 'docker login "$REGISTRY"' in login_action
    assert "is_auth_failure()" in login_action

    assert 'default: "5"' in manifest_action
    assert "Registry probe ${registry}" in manifest_action
    assert 'timeout "${ATTEMPT_TIMEOUT_SECONDS}s" docker manifest inspect "$IMAGE"' in manifest_action
    assert "is_manifest_missing()" in manifest_action
    assert 'echo "exists=true" >> "$GITHUB_OUTPUT"' in manifest_action
    assert 'echo "exists=false" >> "$GITHUB_OUTPUT"' in manifest_action


def test_neurocommand_cache_boundary_uses_build_arg_not_remote_add():
    dockerfile = _read_repo_file("Dockerfile")

    assert "api.github.com/repos/neurodesk/neurocommand/git/refs/heads/main" not in dockerfile
    assert "ARG NEUROCOMMAND_REF=main" in dockerfile
    assert 'git checkout --detach "$NEUROCOMMAND_REF"' in dockerfile


def test_apptainer_dependency_downloads_are_retried():
    dockerfile = _read_repo_file("Dockerfile")

    assert "for attempt in 1 2 3 4 5; do" in dockerfile
    assert "if ./scripts/download-dependencies; then" in dockerfile
    assert 'echo "download-dependencies attempt ${attempt}/5 failed; retrying."' in dockerfile
    assert 'sleep "$((attempt * 10))"' in dockerfile
    assert "&& ./scripts/compile-dependencies" in dockerfile


def test_final_cvmfs_config_step_recreates_config_directory():
    dockerfile = _read_repo_file("Dockerfile")
    step_start = dockerfile.index("# Create cvmfs keys and data directories")
    step_end = dockerfile.index("# Install neurocommand", step_start)
    cvmfs_step = dockerfile[step_start:step_end]

    mkdir_config = "mkdir -p /etc/cvmfs/keys/ardc.edu.au /etc/cvmfs/config.d"
    copy_config = "cp /tmp/cvmfs/neurodesk.ardc.edu.au.conf* /etc/cvmfs/config.d/"

    assert mkdir_config in cvmfs_step
    assert copy_config in cvmfs_step
    assert cvmfs_step.index(mkdir_config) < cvmfs_step.index(copy_config)


def test_cvmfs_runtime_packages_are_protected_from_autoremove():
    dockerfile = _read_repo_file("Dockerfile")
    cleanup_start = dockerfile.index("# Remove build-time packages")
    cleanup_end = dockerfile.index("# The kernel-spec rewrite", cleanup_start)
    cleanup_step = dockerfile[cleanup_start:cleanup_end]

    manual_mark = "apt-mark manual autofs cvmfs uuid-dev"
    purge = "apt-get purge --yes --auto-remove"

    assert manual_mark in cleanup_step
    assert purge in cleanup_step
    assert cleanup_step.index(manual_mark) < cleanup_step.index(purge)


def test_cached_neurocommand_builds_resolve_ref_with_retries():
    for workflow_name in ("build-neurodesktop.yml", "build-neurodesktop-dev.yml"):
        workflow_text = _read_repo_file(f".github/workflows/{workflow_name}")
        resolve_steps = list(_step_bodies(workflow_text, "Resolve neurocommand ref"))

        assert len(resolve_steps) == 1
        resolve_step = resolve_steps[0]
        assert "Authorization: Bearer $GITHUB_TOKEN" in resolve_step
        assert "--retry 5" in resolve_step
        assert "--retry-all-errors" in resolve_step
        assert "api.github.com/repos/neurodesk/neurocommand/git/refs/heads/main" in resolve_step
        assert 'echo "NEUROCOMMAND_REF=$NEUROCOMMAND_REF" >> "$GITHUB_ENV"' in resolve_step

    production_build = next(
        _step_bodies(_read_repo_file(".github/workflows/build-neurodesktop.yml"), "Build and push arch image")
    )
    dev_build = next(
        _step_bodies(_read_repo_file(".github/workflows/build-neurodesktop-dev.yml"), "Build new image")
    )
    dev_push = next(
        _step_bodies(_read_repo_file(".github/workflows/build-neurodesktop-dev.yml"), "Push new image (if changes found)")
    )

    for step_body in (production_build, dev_build, dev_push):
        assert "build-args: |" in step_body
        assert "NEUROCOMMAND_REF=${{ env.NEUROCOMMAND_REF }}" in step_body
