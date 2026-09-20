import os
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
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        grant = "matrix.profile.grant_sudo" if "profile" in matrix else "matrix.grant_sudo"
        assert 'if [ "${{ ' + grant + ' }}" = "packages" ]; then' in commands
        assert "docker exec -u root neurodesktop-test pytest /opt/tests/test_security_policy.py -v" in commands


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
    for path in IMAGE_TEST_WORKFLOWS:
        job = yaml.safe_load(path.read_text())['jobs']['test-image']
        assert set(job['strategy']['matrix']['arch']) == {'amd64', 'arm64'}
        assert 'arm64' in job['runs-on']
        assert 'blacksmith' in job['runs-on']


def test_image_cleanup_is_installed_before_each_runtime_profile_starts():
    for path in IMAGE_TEST_WORKFLOWS:
        job = yaml.safe_load(path.read_text())["jobs"]["test-image"]
        tests = [step["run"] for step in job["steps"]
                 if step.get("name", "").startswith("Test container (")]
        assert len(tests) == 2
        for command in tests:
            assert command.splitlines()[0] == "source .github/scripts/image_test_cleanup.sh"
            assert "test_rc=$?" not in command


def test_image_version_and_publish_tag_share_one_build_timestamp():
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
