import os
import shlex
import subprocess

import yaml

from testlib import repo_path


WORKFLOW = repo_path(".github/workflows/build-neurodesktop.yml")
IMAGE_TEST_WORKFLOWS = (
    WORKFLOW,
    repo_path(".github/workflows/build-neurodesktop-dev.yml"),
    repo_path(".github/workflows/build-neurodesktop-test.yml"),
)
CVMFS_ACTION = (
    "cvmfs-contrib/github-action-cvmfs@"
    "10197e000cc0add8e54ac4fb73d3ed44e2de72b4 # v5.5"
)


def _tag_step(workflow):
    start = workflow.index("      - name: Create github tag\n")
    end = workflow.index("      - name: Write image publish summary\n", start)
    return workflow[start:end]


def _tag_script():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["merge-manifests"]["steps"]
    return next(step["run"] for step in steps if step.get("name") == "Create github tag")


def _run_tag_script(tmp_path, lookup_result, existing_sha=""):
    fake_gh = tmp_path / "gh"
    calls = tmp_path / "calls"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$GH_CALLS"

if [[ "$*" == *"/git/ref/tags/"* ]]; then
  case "$GH_LOOKUP_RESULT" in
    existing)
      printf '%s\n' "$GH_EXISTING_SHA"
      exit 0
      ;;
    missing)
      echo 'gh: Not Found (HTTP 404)' >&2
      exit 1
      ;;
    forbidden)
      echo 'gh: Resource not accessible by integration (HTTP 403)' >&2
      exit 1
      ;;
  esac
fi

if [[ "$*" == *"--method PATCH"* || "$*" == *"--method POST"* ]]; then
  exit 0
fi

echo "Unexpected gh invocation: $*" >&2
exit 2
"""
    )
    fake_gh.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}{os.pathsep}{env['PATH']}",
            "GH_CALLS": str(calls),
            "GH_LOOKUP_RESULT": lookup_result,
            "GH_EXISTING_SHA": existing_sha,
            "GITHUB_REPOSITORY": "neurodesk/neurodesktop",
            "GITHUB_SHA": "new-sha",
            "BUILDDATE": "2026-08-12",
        }
    )
    result = subprocess.run(
        ["bash", "-c", _tag_script()],
        capture_output=True,
        env=env,
        text=True,
    )
    return result, calls.read_text()


def test_production_build_serializes_shared_publish_tags():
    workflow = WORKFLOW.read_text()

    assert "concurrency:\n  group: build-neurodesktop-publish\n" in workflow
    assert "  cancel-in-progress: false\n" in workflow


def test_production_build_checkouts_stay_on_the_run_sha():
    workflow = WORKFLOW.read_text()
    checkout_count = workflow.count("uses: actions/checkout@")

    assert checkout_count > 0
    assert workflow.count("ref: ${{ github.sha }}") == checkout_count
    assert "ref: ${{ github.ref }}" not in workflow


def test_image_tests_use_cvmfs_action_without_stale_apt_package_lists():
    for workflow_path in IMAGE_TEST_WORKFLOWS:
        workflow = workflow_path.read_text()

        assert CVMFS_ACTION in workflow
        assert "github-action-cvmfs@v3" not in workflow


def test_image_workflows_exercise_package_policy_and_second_uid_isolation():
    for path in IMAGE_TEST_WORKFLOWS:
        workflow = yaml.safe_load(path.read_text())
        job = workflow["jobs"]["test-image"]
        matrix = job["strategy"]["matrix"]
        profiles = matrix.get("include", matrix.get("profile"))
        assert any(profile["grant_sudo"] == "packages" for profile in profiles)
        assert any(profile["grant_sudo"] == "yes" for profile in profiles)


def test_production_build_updates_the_date_tag_without_deleting_it():
    tag_step = _tag_step(WORKFLOW.read_text())

    assert "--method DELETE" not in tag_step
    assert '"/repos/${GITHUB_REPOSITORY}/git/ref/${tag_path}"' in tag_step
    assert "--method PATCH" in tag_step
    assert "-F force=true" in tag_step
    assert "--method POST" in tag_step
    assert "\\(HTTP 404\\)" in tag_step
    assert '"status"[[:space:]]*:[[:space:]]*"?404"?' in tag_step
    assert 'exit "$lookup_rc"' in tag_step


def test_tag_script_creates_a_missing_tag_without_a_delete(tmp_path):
    result, calls = _run_tag_script(tmp_path, "missing")

    assert result.returncode == 0, result.stderr
    assert "/git/ref/tags/2026-08-12" in calls
    assert "--method POST" in calls
    assert "--method PATCH" not in calls
    assert "--method DELETE" not in calls


def test_tag_script_atomically_updates_an_existing_tag(tmp_path):
    result, calls = _run_tag_script(tmp_path, "existing", "old-sha")

    assert result.returncode == 0, result.stderr
    assert "--method PATCH" in calls
    assert "force=true" in calls
    assert "--method POST" not in calls
    assert "--method DELETE" not in calls


def test_tag_script_does_not_mutate_after_a_lookup_auth_failure(tmp_path):
    result, calls = _run_tag_script(tmp_path, "forbidden")

    assert result.returncode == 1
    assert "HTTP 403" in result.stderr
    assert "--method PATCH" not in calls
    assert "--method POST" not in calls
    assert "--method DELETE" not in calls


def test_release_tags_wait_for_validation_of_run_specific_candidates():
    """Prevent publishing a different image from the candidate that passed validation."""
    for path in IMAGE_TEST_WORKFLOWS:
        workflow = yaml.safe_load(path.read_text())
        jobs = workflow['jobs']
        publish = jobs['merge-manifests']
        assert set(publish['needs']) == {'prepare-build', 'build-image', 'test-image', 'scan-image'}
        assert 'if' not in publish  # Default success() must reject failed or skipped checks.
        build = next(s for s in jobs['build-image']['steps']
                     if s.get('uses', '').startswith('docker/build-push-action@'))
        assert build['with']['tags'] == '${{ env.IMAGEID }}:run-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.platform.arch }}'
        for job_id in ('test-image', 'scan-image', 'merge-manifests'):
            steps = jobs[job_id]['steps']
            commands = '\n'.join(s.get('run', '') for s in steps)
            assert 'run-${{ github.run_id }}-${{ github.run_attempt }}-' in commands
        for job in jobs.values():
            for step in job['steps']:
                if step.get('uses', '').startswith('actions/checkout@'):
                    assert step['with']['ref'] == '${{ github.sha }}'
        assert not any(s.get('name') == 'Check if image exists'
                       for s in jobs['build-image']['steps'])


def test_every_image_flavor_runs_native_arm64_runtime_checks():
    """Require runtime coverage on native runners for both published architectures."""
    for path in IMAGE_TEST_WORKFLOWS:
        job = yaml.safe_load(path.read_text())['jobs']['test-image']
        assert set(job['strategy']['matrix']['arch']) == {'amd64', 'arm64'}
        assert 'arm64' in job['runs-on']
        assert 'blacksmith' in job['runs-on']


def test_every_image_flavor_invokes_the_runtime_validator_with_its_profile():
    for path in IMAGE_TEST_WORKFLOWS:
        workflow = yaml.safe_load(path.read_text())
        job = workflow["jobs"]["test-image"]
        tests = [step for step in job["steps"]
                 if step.get("name", "").startswith("Test container (")]
        assert len(tests) == 2
        assert "shell" not in workflow.get("defaults", {}).get("run", {})
        assert "shell" not in job.get("defaults", {}).get("run", {})
        assert all("shell" not in step for step in tests)
        assert [step["if"] for step in tests] == [
            "${{ ! matrix.profile.hpc_mode }}", "${{ matrix.profile.hpc_mode }}",
        ]
        commands = [shlex.split(step["run"].replace("\\\n", " ")) for step in tests]
        assert commands == [
            ["exec", "bash", ".github/scripts/validate_image_runtime.sh", "$IMAGE_REF", "regular",
             "${{ matrix.profile.cvmfs_disable }}", "${{ matrix.profile.grant_sudo }}",
             "${{ matrix.profile.needs_cvmfs }}"],
            ["exec", "bash", ".github/scripts/validate_image_runtime.sh", "$IMAGE_REF", "hpc"],
        ]


def test_image_version_and_publish_tag_share_one_build_timestamp():
    """Keep embedded versions and release tags consistent across date boundaries."""
    for path in IMAGE_TEST_WORKFLOWS:
        jobs = yaml.safe_load(path.read_text())["jobs"]
        prepare = jobs["prepare-build"]
        assert prepare["outputs"]["build_date"] == "${{ steps.metadata.outputs.build_date }}"
        for job_id in ("build-image", "merge-manifests"):
            needs = jobs[job_id]["needs"]
            assert "prepare-build" in ([needs] if isinstance(needs, str) else needs)
            script = next(step["run"] for step in jobs[job_id]["steps"]
                          if step.get("name") == "Set environment variables")
            assert 'BUILDDATE="${{ needs.prepare-build.outputs.build_date }}"' in script
            assert "$(date" not in script


def test_candidate_pulls_retry_a_transient_registry_failure(tmp_path):
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [ ! -e "$DOCKER_FAILED_ONCE" ]; then
    touch "$DOCKER_FAILED_ONCE"
    echo 'Error response from daemon: Head "https://ghcr.io/v2/x/manifests/y": EOF' >&2
    exit 1
fi
"""
    )
    fake_docker.chmod(0o755)
    expressions = {
        "${{ github.run_id }}": "123",
        "${{ github.run_attempt }}": "1",
        "${{ matrix.arch }}": "amd64",
    }
    for path in IMAGE_TEST_WORKFLOWS:
        workflow = yaml.safe_load(path.read_text())
        for job in ("test-image", "scan-image"):
            steps = workflow["jobs"][job]["steps"]
            script = next(step["run"] for step in steps if step.get("name") == "Pull container image")
            for expression, value in expressions.items():
                script = script.replace(expression, value)
            calls = tmp_path / f"{path.stem}-{job}.calls"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{tmp_path}{os.pathsep}{env['PATH']}",
                    "DOCKER_CALLS": str(calls),
                    "DOCKER_FAILED_ONCE": str(tmp_path / f"{path.stem}-{job}.failed"),
                    "IMAGEID": "ghcr.io/neurodesk/candidate",
                    "IMAGE_REF": "ghcr.io/neurodesk/candidate:run-123-1-amd64",
                    "RETRY_DELAY": "0",
                }
            )

            result = subprocess.run(
                ["bash", "-e", "-c", script],
                capture_output=True,
                cwd=repo_path("."),
                env=env,
                text=True,
            )

            assert result.returncode == 0, (path.name, job, result.stderr)
            assert calls.read_text() == "pull ghcr.io/neurodesk/candidate:run-123-1-amd64\n" * 2


def test_cvmfs_image_tests_report_the_active_mirror_after_a_failure(tmp_path):
    calls = tmp_path / "calls"
    for name in ("sudo", "cvmfs_config"):
        shim = tmp_path / name
        shim.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$CALLS"\n')
        shim.chmod(0o755)
    for path in IMAGE_TEST_WORKFLOWS:
        steps = yaml.safe_load(path.read_text())["jobs"]["test-image"]["steps"]
        names = [step.get("name") for step in steps]
        report = steps[names.index("Report scientific mount status")]
        assert names.index("Report scientific mount status") > names.index(
            "Test container (HPC Apptainer simulation)"), path.name
        assert report["if"] == "always() && matrix.profile.needs_cvmfs", path.name
        calls.unlink(missing_ok=True)
        result = subprocess.run(
            ["bash", "-e", "-c", report["run"]],
            env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
                 "CALLS": str(calls)},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.read_text().splitlines() == [
            "timeout 20 cvmfs_talk -i neurodesk.ardc.edu.au host info",
            "timeout 20 cvmfs_config stat -v neurodesk.ardc.edu.au",
        ], path.name
