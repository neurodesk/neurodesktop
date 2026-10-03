"""Execute workflow runtime validation with local command adapters."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
import yaml

from testlib import repo_path


WORKFLOWS = [
    repo_path(f".github/workflows/build-neurodesktop{suffix}.yml")
    for suffix in ("", "-test", "-dev")
]
IMAGE = "ghcr.io/neurodesk/candidate:run-101-2-amd64"


@pytest.fixture
def validate(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    resources = tmp_path / "resources"
    resources.mkdir()
    trace = tmp_path / "docker.jsonl"
    docker = commands / "docker"
    docker.write_text(
        f"#!{sys.executable} -S\n" + r'''
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
record = {"args": args}
if args[:2] == ["run", "-d"] and "--privileged" not in args:
    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "-v"]
    record["resources"] = [mount.split(":", 1)[0] for mount in mounts]
    record["passwd"] = Path(record["resources"][1]).read_text()
    record["group"] = Path(record["resources"][2]).read_text()
    record["home_mode"] = Path(record["resources"][0]).stat().st_mode & 0o777
    record["identity_modes"] = [Path(path).stat().st_mode & 0o777 for path in record["resources"][1:]]
with open(os.environ["DOCKER_TRACE"], "a") as stream:
    stream.write(json.dumps(record) + "\n")

if args[0] == "rm":
    sys.exit(int(os.environ.get("CLEANUP_STATUS", "0")))
if args[0] == "run":
    if "--entrypoint" in args:
        sys.exit(int(os.environ.get("OWNERSHIP_STATUS", "0")))
    print("test-container")
    sys.exit(int(os.environ.get("START_STATUS", "0")))
if args[0] == "inspect":
    print(os.environ.get("INSPECT_OUTPUT", "false" if os.environ.get("CONTAINER_EXITED") == "1" else "true"))
    sys.exit(int(os.environ.get("INSPECT_STATUS", "0")))
if args[0] == "logs":
    print("test container startup log")
    sys.exit(int(os.environ.get("LOG_STATUS", "0")))
if args[0] == "exec":
    if "curl" in args:
        if "PROBE_MARKER" in os.environ:
            Path(os.environ["PROBE_MARKER"]).touch()
            while not Path(os.environ["PROBE_RELEASE"]).exists():
                time.sleep(0.01)
        responses = json.loads(os.environ.get("HTTP_RESPONSES", "[]"))
        if responses:
            probes = sum("curl" in json.loads(line)["args"]
                         for line in Path(os.environ["DOCKER_TRACE"]).read_text().splitlines())
            response = responses[min(probes - 1, len(responses) - 1)]
        else:
            response = os.environ.get("HTTP_STATUS", "403")
        sys.stdout.write(response)
        sys.exit(int(os.environ.get("CURL_STATUS", "0")))
    if "pytest" in args:
        security = "/opt/tests/test_security_policy.py" in args
        sys.exit(int(os.environ.get("SECURITY_STATUS" if security else "TEST_STATUS", "0")))
print("Unexpected Docker command", args, file=sys.stderr)
sys.exit(99)
'''
    )
    docker.chmod(0o755)
    sleep = commands / "sleep"
    sleep.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SLEEP_TRACE"\n')
    sleep.chmod(0o755)
    mktemp = commands / "mktemp"
    mktemp.write_text('''#!/usr/bin/env bash
echo allocation >> "$ALLOCATION_TRACE"
if [ "${FAIL_ALLOCATION:-}" = "$(wc -l < "$ALLOCATION_TRACE")" ]; then exit 31; fi
exec /usr/bin/mktemp "$@"
''')
    mktemp.chmod(0o755)
    rm = commands / "rm"
    rm.write_text('''#!/usr/bin/env bash
if [ "${DELETE_STATUS:-0}" != 0 ]; then exit "$DELETE_STATUS"; fi
exec /bin/rm "$@"
''')
    rm.chmod(0o755)

    def run(workflow=WORKFLOWS[0], *, hpc=False, grant_sudo="no", cvmfs=False,
            cancel_scope=None, **scenario):
        steps = yaml.safe_load(workflow.read_text())["jobs"]["test-image"]["steps"]
        step = next(
            step for step in steps
            if step.get("name", "").startswith("Test container (")
            and ("HPC" in step["name"]) == hpc
        )
        values = {
            "matrix.profile.grant_sudo": grant_sudo,
            "matrix.profile.needs_cvmfs": str(cvmfs).lower(),
            "matrix.profile.cvmfs_disable": str(not cvmfs).lower(),
            "matrix.profile.hpc_mode": str(hpc).lower(),
        }

        def expand(value):
            for expression, replacement in values.items():
                value = value.replace("${{ " + expression + " }}", replacement)
            assert "${{" not in value
            return value

        env = {
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TMPDIR": str(resources),
            "IMAGE_REF": IMAGE,
            "DOCKER_TRACE": str(trace),
            "SLEEP_TRACE": str(tmp_path / "sleep.log"),
            "ALLOCATION_TRACE": str(tmp_path / "allocation.log"),
            **{key: expand(str(value)) for key, value in step.get("env", {}).items()},
            **{key: str(value) for key, value in scenario.items()},
        }
        script = tmp_path / "step.sh"
        script.write_text(expand(step["run"]))
        if cancel_scope:
            marker = tmp_path / "probe-started"
            release = tmp_path / "probe-release"
            env.update(PROBE_MARKER=str(marker), PROBE_RELEASE=str(release))
        process = subprocess.Popen(
            ["bash", "-e", str(script)], cwd=repo_path("."),
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        try:
            if cancel_scope:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert marker.exists(), "validation never reached its readiness probe"
                send = os.kill if cancel_scope == "shell" else os.killpg
                send(process.pid, signal.SIGTERM)
                release.touch()
            stdout, stderr = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        assert trace.exists(), (result.stdout, result.stderr)
        records = [json.loads(line) for line in trace.read_text().splitlines()]
        return result, records, resources

    return run


def pytest_calls(records):
    return [record["args"] for record in records
            if record["args"][0] == "exec" and "pytest" in record["args"]]


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.stem)
@pytest.mark.parametrize("hpc", [False, True], ids=["regular", "hpc"])
def test_authenticated_http_response_runs_tests_and_cleans_up(validate, workflow, hpc):
    result, records, resources = validate(workflow, hpc=hpc)

    assert result.returncode == 0, result.stderr
    assert len(pytest_calls(records)) == 1
    if hpc:
        startup = next(record for record in records if "resources" in record)
        assert records[-1]["args"] == [
            "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
            "-v", f"{startup['resources'][0]}:/cleanup", IMAGE,
            "-R", f"{os.getuid()}:{os.getgid()}", "/cleanup",
        ]
    else:
        assert records[-1]["args"] == ["rm", "-f", "neurodesktop-test"]
    assert not list(resources.iterdir())
    assert not (resources.parent / "sleep.log").exists()


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.stem)
@pytest.mark.parametrize("hpc", [False, True], ids=["regular", "hpc"])
def test_readiness_exhaustion_preserves_regular_fallthrough_and_hpc_failure(validate, workflow, hpc):
    result, records, resources = validate(workflow, hpc=hpc, HTTP_STATUS="000")

    assert result.returncode == (1 if hpc else 0), result.stderr
    probes = [record for record in records if "curl" in record["args"]]
    assert len(probes) == (90 if hpc else 60)
    assert (resources.parent / "sleep.log").read_text().splitlines() == ["2"] * len(probes)
    assert len(pytest_calls(records)) == (0 if hpc else 1)
    assert any(record["args"] == ["rm", "-f", "neurodesktop-test"] for record in records[2:])
    assert not list(resources.iterdir())
    if hpc:
        assert "did not reach /api/status" in result.stdout
        assert any(record["args"][0] == "logs" for record in records)


def test_regular_profile_preserves_cvmfs_privileges_and_package_security_order(validate):
    result, records, _ = validate(grant_sudo="packages", cvmfs=True)

    assert result.returncode == 0, result.stderr
    command = next(record["args"] for record in records if record["args"][:2] == ["run", "-d"])
    assert command == [
        "run", "-d", "--shm-size=1gb", "--privileged", "--user=root",
        "--name", "neurodesktop-test", "-v", "/cvmfs:/cvmfs:shared",
        "-e", "CVMFS_DISABLE=false", "-e", "GRANT_SUDO=packages",
        "-e", "NEURODESKTOP_CVMFS_STARTUP_MODE=eager",
        "-e", f"NB_UID={os.getuid()}", "-e", f"NB_GID={os.getgid()}", IMAGE,
    ]
    security, suite = pytest_calls(records)
    assert security == ["exec", "-u", "root", "neurodesktop-test", "pytest", "/opt/tests/test_security_policy.py", "-v"]
    assert suite == [
        "exec", "-e", "NEURODESKTOP_TEST_ALLOW_GLOBAL_DESKTOP_SERVICES=1",
        "-u", "jovyan", "neurodesktop-test", "pytest", "/opt/tests/", "-v",
    ]


def test_hpc_profile_preserves_foreign_identity_and_removes_temporary_files(validate):
    result, records, resources = validate(hpc=True)

    assert result.returncode == 0, result.stderr
    startup = next(record for record in records if record["args"][:2] == ["run", "-d"])
    command = startup["args"]
    home, passwd, group = startup["resources"]
    assert command == [
        "run", "-d", "--shm-size=1gb", "--user", "5000:5000", "--name", "neurodesktop-test",
        "-v", f"{home}:/home/jovyan", "-v", f"{passwd}:/etc/passwd:ro",
        "-v", f"{group}:/etc/group:ro", "-e", "CVMFS_DISABLE=true",
        "-e", "NB_USER=sciget", "-e", "NB_UID=5000", "-e", "NB_GID=5000",
        "-e", "HOME=/home/jovyan", "-e", "USER=sciget", "-e", "LOGNAME=sciget",
        "-e", "APPTAINER_CONTAINER=1", "-e", "APPTAINER_NAME=neurodesktop-test-hpc",
        "-e", "NEURODESKTOP_CVMFS_STARTUP_MODE=lazy", "-e", "NEURODESKTOP_SLURM_ENABLE=0",
        "-e", "NEURODESKTOP_SLURM_STARTUP_MODE=eager", IMAGE,
    ]
    assert startup["passwd"] == (
        "root:x:0:0:root:/root:/bin/bash\n"
        "jovyan:x:1000:100:jovyan:/home/jovyan:/bin/bash\n"
        "sciget:x:5000:5000:sciget (HPC simulated):/home/jovyan:/bin/bash\n"
        "nobody:x:65534:65534:nobody:/:/usr/sbin/nologin\n"
    )
    assert startup["group"] == (
        "root:x:0:\nusers:x:100:jovyan,sciget\nsciget:x:5000:\nnogroup:x:65534:\n"
    )
    assert startup["home_mode"] == 0o777
    assert startup["identity_modes"] == [0o644, 0o644]
    assert pytest_calls(records)[0] == [
        "exec", "-e", "NEURODESKTOP_TEST_ALLOW_GLOBAL_DESKTOP_SERVICES=1",
        "neurodesktop-test", "pytest", "/opt/tests/", "-v",
    ]
    assert all(not Path(path).exists() for path in startup["resources"])
    assert not list(resources.iterdir())


@pytest.mark.parametrize("hpc", [False, True])
@pytest.mark.parametrize("failure,expected", [("START_STATUS", 19), ("TEST_STATUS", 23)])
def test_failure_status_survives_cleanup(validate, hpc, failure, expected):
    result, records, resources = validate(hpc=hpc, CLEANUP_STATUS=7, **{failure: expected})

    assert result.returncode == expected
    assert not list(resources.iterdir())
    assert any(record["args"] == ["rm", "-f", "neurodesktop-test"] for record in records[2:])
    if failure == "START_STATUS":
        assert not pytest_calls(records)


def test_package_security_failure_prevents_suite(validate):
    result, records, _ = validate(grant_sudo="packages", SECURITY_STATUS=29)

    assert result.returncode == 29
    assert len(pytest_calls(records)) == 1
    assert "/opt/tests/test_security_policy.py" in pytest_calls(records)[0]
    assert records[-1]["args"] == ["rm", "-f", "neurodesktop-test"]


def test_hpc_exit_during_startup_does_not_run_tests(validate):
    result, records, resources = validate(hpc=True, CONTAINER_EXITED=1)

    assert result.returncode == 1
    assert "exited during startup" in result.stdout
    assert not pytest_calls(records)
    assert not any("curl" in record["args"] for record in records)
    assert not list(resources.iterdir())


@pytest.mark.parametrize("hpc", [False, True])
def test_cleanup_failure_rejects_otherwise_successful_validation(validate, hpc):
    result, _, resources = validate(hpc=hpc, CLEANUP_STATUS=7)

    assert result.returncode == 1
    assert not list(resources.iterdir())


@pytest.mark.parametrize("hpc", [False, True])
def test_multiline_failed_curl_preserves_existing_acceptance(validate, hpc):
    result, records, _ = validate(hpc=hpc, HTTP_STATUS="000\n", CURL_STATUS=7)

    assert result.returncode == 0, result.stderr
    assert len([record for record in records if "curl" in record["args"]]) == 1
    assert len(pytest_calls(records)) == 1


@pytest.mark.parametrize("hpc", [False, True])
@pytest.mark.parametrize("response,curl_status", [("000", 7), ("malformed", 0)])
def test_unready_curl_output_keeps_each_timeout_policy(validate, hpc, response, curl_status):
    result, records, _ = validate(hpc=hpc, HTTP_STATUS=response, CURL_STATUS=curl_status)

    assert result.returncode == (1 if hpc else 0)
    assert len([record for record in records if "curl" in record["args"]]) == (90 if hpc else 60)
    assert len(pytest_calls(records)) == (0 if hpc else 1)


@pytest.mark.parametrize("hpc", [False, True])
def test_delayed_readiness_stops_polling_after_the_first_accepted_response(validate, hpc):
    result, records, resources = validate(hpc=hpc, HTTP_RESPONSES=json.dumps(["000", "garbage", "403"]))

    assert result.returncode == 0, result.stderr
    assert len([record for record in records if "curl" in record["args"]]) == 3
    assert (resources.parent / "sleep.log").read_text().splitlines() == ["2", "2"]
    assert len(pytest_calls(records)) == 1


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.stem)
@pytest.mark.parametrize("hpc", [False, True], ids=["regular", "hpc"])
@pytest.mark.parametrize("scope", ["shell", "group"])
def test_cancellation_cleans_up_without_running_tests(validate, workflow, hpc, scope):
    result, records, resources = validate(workflow, hpc=hpc, cancel_scope=scope)

    assert result.returncode == -signal.SIGTERM
    assert not pytest_calls(records)
    assert any(record["args"] == ["rm", "-f", "neurodesktop-test"] for record in records[2:])
    assert not list(resources.iterdir())


@pytest.mark.parametrize("primary_status", [0, 23])
def test_hpc_ownership_failure_still_deletes_files_and_preserves_test_failure(validate, primary_status):
    result, _, resources = validate(hpc=True, OWNERSHIP_STATUS=7, TEST_STATUS=primary_status)

    assert result.returncode == (primary_status or 1)
    assert "Could not restore ownership" in result.stderr
    assert not list(resources.iterdir())


@pytest.mark.parametrize("allocation", [1, 2, 3])
def test_hpc_allocation_failure_cleans_up_owned_paths(validate, allocation):
    result, records, resources = validate(hpc=True, FAIL_ALLOCATION=allocation)

    assert result.returncode == 31
    assert not pytest_calls(records)
    assert not list(resources.iterdir())


def test_hpc_inspect_failure_keeps_liveness_failure_despite_failed_diagnostics(validate):
    result, records, resources = validate(hpc=True, INSPECT_OUTPUT="false", INSPECT_STATUS=17, LOG_STATUS=18)

    assert result.returncode == 1
    assert not pytest_calls(records)
    assert not any("curl" in record["args"] for record in records)
    assert ["logs", "--tail", "120", "neurodesktop-test"] in [record["args"] for record in records]
    assert not list(resources.iterdir())


@pytest.mark.parametrize("output", ["true", "prefix-true-suffix"])
def test_hpc_liveness_preserves_grep_success_despite_inspect_failure(validate, output):
    result, records, resources = validate(hpc=True, INSPECT_OUTPUT=output, INSPECT_STATUS=17)

    assert result.returncode == 0, result.stderr
    assert len(pytest_calls(records)) == 1
    assert not any(record["args"][0] == "logs" for record in records)
    assert not list(resources.iterdir())


@pytest.mark.parametrize("primary_status", [0, 23])
def test_hpc_deletion_failure_rejects_success_and_preserves_test_failure(validate, primary_status):
    result, _, resources = validate(hpc=True, DELETE_STATUS=7, TEST_STATUS=primary_status)

    assert result.returncode == (primary_status or 1)
    assert "Could not remove HPC test files" in result.stderr
    assert len(list(resources.iterdir())) == 3


def test_regular_validation_does_not_clean_up_ambient_hpc_paths(validate, tmp_path):
    sentinel = tmp_path / "user-home"
    sentinel.mkdir()
    user_file = sentinel / "keep"
    user_file.write_text("user-owned")
    result, _, _ = validate(HPC_HOME_DIR=sentinel, HPC_PASSWD_FILE=user_file, HPC_GROUP_FILE=user_file)

    assert result.returncode == 0, result.stderr
    assert user_file.read_text() == "user-owned"


@pytest.mark.parametrize("args", [
    [], [IMAGE], [IMAGE, "other"], [IMAGE, "hpc", "extra"],
    [IMAGE, "regular", "true", "packages"],
    [IMAGE, "regular", "true", "all", "false"],
    [IMAGE, "regular", "maybe", "no", "false"],
    [IMAGE, "regular", "true", "no", "maybe"],
])
def test_invalid_request_has_no_container_side_effects(tmp_path, args):
    marker = tmp_path / "docker-called"
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\ntouch "$DOCKER_MARKER"\nexit 99\n')
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(repo_path(".github/scripts/validate_image_runtime.sh")), *args],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "DOCKER_MARKER": str(marker)},
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not marker.exists()
