"""Exercise submission crash windows, scheduler evidence and archive boundaries."""

import concurrent.futures
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

import pytest
from testlib import repo_path


@pytest.fixture
def jobs(monkeypatch):
    monkeypatch.syspath_prepend(str(repo_path("config/remote_jobs")))
    from neurodesk_jobs import client, core, remote, transport
    return client, core, remote, transport


@pytest.fixture
def site(tmp_path):
    return {"name": "fixture", "host": "fixture", "transfer_host": "fixture",
            "state_root": str(tmp_path / "journal"), "store_root": str(tmp_path / "store"),
            "work_root": str(tmp_path / "work"), "archive_root": str(tmp_path / "archive"),
            "path": os.environ["PATH"], "python": sys.executable,
            "apptainer": "/usr/bin/apptainer", "scratch_retention_days": 1,
            "resources": {"time": "00:05:00", "mem": "1G", "cpus-per-task": 1}}


@pytest.fixture
def short_home(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="nj-home-", dir="/tmp") as home:
        monkeypatch.setattr(Path, "home", lambda: Path(home))
        yield Path(home)


@pytest.fixture
def scheduler(jobs, site, tmp_path, monkeypatch):
    _, core, remote, _ = jobs
    binaries = tmp_path / "bin"
    binaries.mkdir()
    accepted = tmp_path / "accepted"
    script = binaries / "sbatch"
    script.write_text(f"""#!{sys.executable}
import os, pathlib, sys, time
pathlib.Path({str(accepted)!r}).open('a').write('accepted\\n')
time.sleep(0.1)
sys.stdin.read()
if os.environ.get('LOSE_RESPONSE'):
    sys.exit(1)
print('1234')
""")
    script.chmod(0o755)
    for tool in ("squeue", "sacct"):
        script = binaries / tool
        script.write_text(f"#!{sys.executable}\nimport os\nprint(os.environ.get('{tool.upper()}', ''))\n")
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + ":" + os.environ["PATH"])
    value = {"site": site, "run_id": "1" * 32, "request_digest": "2" * 64,
             "input_id": "3" * 32, "input_commit": "4" * 40, "helper": "/helper.pyz"}
    remote.prepare(value)
    return value, accepted, core, remote


def test_simultaneous_submission_attempts_call_sbatch_once(scheduler):
    value, accepted, _, remote = scheduler
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(remote.submit, [value] * 4))
    assert accepted.read_text().splitlines() == ["accepted"]
    assert any(reply.get("job_id") == "1234" for reply in replies)
    assert remote.submit(value)["job_id"] == "1234"


def test_lost_acceptance_never_resubmits_and_accounting_recovers(scheduler, monkeypatch):
    value, accepted, _, remote = scheduler
    monkeypatch.setenv("LOSE_RESPONSE", "1")
    assert remote.submit(value)["state"] == "submission-unknown"
    assert remote.submit(value)["state"] == "submission-unknown"
    assert remote.status(value)["scheduler"]["state"] == "UNKNOWN"
    assert accepted.read_text().splitlines() == ["accepted"]
    import getpass
    monkeypatch.setenv("SACCT", f"1234|nj-{value['run_id']}|nj:{value['run_id']}:{value['request_digest']}|COMPLETED|0:0|{getpass.getuser()}")
    observed = remote.status(value)
    assert observed["scheduler"]["job_id"] == "1234"
    assert observed["scheduler"]["state"] == "COMPLETED"
    assert remote.submit(value)["state"] == "submission-unknown"
    assert accepted.read_text().splitlines() == ["accepted"]


def test_stale_claim_or_missing_journal_cannot_submit(scheduler):
    value, accepted, _, remote = scheduler
    directory = remote.run_directory(value["site"], value["run_id"])
    (directory / "submit.claim").mkdir()
    assert remote.submit(value)["state"] == "submission-unknown"
    assert not accepted.exists()
    (directory / "request.json").unlink()
    with pytest.raises(Exception, match="journal missing"):
        remote.submit(value)
    assert not accepted.exists()


def test_manifest_reuse_and_reprepare_rejected(scheduler):
    value, accepted, _, remote = scheduler
    with pytest.raises(Exception, match="different request"):
        remote.prepare(dict(value, input_commit="a" * 40))
    with pytest.raises(Exception, match="does not match"):
        remote.submit(dict(value, request_digest="f" * 64))
    assert not accepted.exists()


def test_queue_evidence_overrides_stale_completed_accounting(scheduler, monkeypatch):
    value, _, _, remote = scheduler
    remote.submit(value)
    record = remote.status(value)["submission"]
    monkeypatch.setenv("SQUEUE", f"1234|{record['job_name']}|{record['comment']}|PENDING")
    monkeypatch.setenv("SACCT", f"1234|{record['job_name']}|{record['comment']}|COMPLETED|0:0|{record['owner']}")
    assert remote.status(value)["scheduler"]["state"] == "PENDING"


def test_accounting_recovers_utc_job_before_site_local_midnight(scheduler, monkeypatch):
    value, _, core, remote = scheduler
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    record = remote.submit(value)
    record["created"] = "2026-11-05T01:00:00"
    directory = remote.run_directory(value["site"], value["run_id"])
    core.atomic(directory / "submission.json", record)
    row = f"1234|{record['job_name']}|{record['comment']}|COMPLETED|0:0|{record['owner']}"
    executable = Path(os.environ["PATH"].split(":")[0]) / "sacct"
    executable.write_text(f"""#!{sys.executable}
import calendar, sys, time
time.tzset()
start = sys.argv[sys.argv.index('--starttime') + 1]
boundary = time.mktime(time.strptime(start, '%Y-%m-%d'))
completed = calendar.timegm((2026, 11, 5, 1, 15, 0))
if boundary <= completed:
    print({row!r})
""")
    observed = remote.status(value)
    assert observed["scheduler"]["state"] == "COMPLETED"
    assert observed["scheduler"]["job_id"] == "1234"


def test_preparation_retry_repairs_missing_attempt_directory(scheduler):
    value, accepted, core, remote = scheduler
    directory = remote.run_directory(value["site"], value["run_id"])
    (directory / "attempts").rmdir()
    assert remote.prepare(value) == {"prepared": True}
    assert core.read(directory / "request.json") == value
    assert remote.status(value)["attempts"] == []
    assert not accepted.exists()
    assert (directory / "attempts").is_dir()


@pytest.mark.parametrize("state,exit_code", [("UNKNOWN", "0:0"), ("FAILED", "1:0"),
                                            ("CANCELLED", "0:0"), ("TIMEOUT", "0:0"),
                                            ("OUT_OF_MEMORY", "0:0"), ("COMPLETED", "0:9")])
def test_receipt_does_not_override_scheduler_failure(jobs, site, tmp_path, monkeypatch, short_home, state, exit_code):
    client, core, _, _ = jobs
    cli = client.Client(site, tmp_path / "client")
    run_id = "a" * 32
    core.atomic(cli.directory(run_id) / "run.json", {"site": site})
    monkeypatch.setattr(cli, "status", lambda _: {
        "scheduler": {"state": state, "exit_code": exit_code},
        "attempts": [{"state": "site-results-available", "receipt": {}}]})
    with pytest.raises(core.JobError, match="confirmed Slurm"):
        cli.collect(run_id)


def test_cancel_requires_correlated_job(scheduler):
    value, _, core, remote = scheduler
    with pytest.raises(core.JobError, match="uncorrelated"):
        remote.cancel(value)


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE),
                                      ("/absolute", tarfile.REGTYPE),
                                      ("symlink", tarfile.SYMTYPE),
                                      ("hardlink", tarfile.LNKTYPE)])
def test_restore_rejects_unsafe_archive(jobs, tmp_path, name, kind):
    client, core, _, _ = jobs
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.linkname = "../escape"
        archive.addfile(entry, io.BytesIO())
    with pytest.raises(core.JobError):
        client.restore(path, tmp_path / "restored")
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "restored").exists()


def test_profile_templates_require_real_site_configuration(jobs):
    _, core, _, _ = jobs
    for site in ("bunya", "sherlock"):
        value = core.read(repo_path(f"config/remote_jobs/profiles/{site}.json"))
        with pytest.raises(core.JobError, match="Configure an absolute"):
            core.profile(value)


def test_transport_is_noninteractive_and_private(jobs, site, tmp_path, short_home):
    _, _, _, transport = jobs
    ssh = transport.SSH(site, tmp_path)
    assert "BatchMode=yes" in ssh.options
    assert "ForwardAgent=no" in ssh.options
    assert ssh.sockets.stat().st_mode & 0o077 == 0
    assert ssh.env["DATALAD_SSH_MULTIPLEX__CONNECTIONS"] == "false"
    assert "annex.sshcaching" in ssh.env.values()


def test_connect_does_not_require_confirmation_for_each_reused_session(jobs, site, tmp_path, short_home, monkeypatch):
    _, _, _, transport = jobs
    ssh = transport.SSH(site, tmp_path)
    real_run = subprocess.run
    configurations = []

    def inspect_configuration(argv, **kwargs):
        if "check" in argv:
            return subprocess.CompletedProcess(argv, 255)
        result = real_run([argv[0], "-G", *argv[1:]], capture_output=True, text=True, check=True)
        configurations.append(result.stdout)
        return result

    monkeypatch.setattr(transport.subprocess, "run", inspect_configuration)
    ssh.connect()
    assert len(configurations) == 1
    assert "controlmaster true\n" in configurations[0] or "controlmaster yes\n" in configurations[0]
    assert "controlmaster ask\n" not in configurations[0]


def test_cli_help_runs_without_site_or_connection():
    result = subprocess.run([sys.executable, "-m", "neurodesk_jobs.cli", "--help"],
                            env=dict(os.environ, PYTHONPATH=str(repo_path("config/remote_jobs"))),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "prepare" in result.stdout and "archive" in result.stdout


def test_image_packages_cli_and_site_templates():
    dockerfile = repo_path("Dockerfile").read_text()
    assert "source=config/remote_jobs,target=/tmp/remote_jobs,ro" in dockerfile
    assert "pip install --no-deps /tmp/neurodesk-jobs-build" in dockerfile
    assert "/usr/local/bin/neurodesk-jobs" in dockerfile
    assert "/opt/neurodesktop/remote-jobs/profiles/" in dockerfile


def test_help_describes_public_commands_and_hides_private_entry_points():
    result = subprocess.run([sys.executable, "-m", "neurodesk_jobs.cli", "--help"],
                            cwd=repo_path("config/remote_jobs"), capture_output=True,
                            text=True, check=True)
    assert result.stdout.startswith("usage: neurodesk jobs ")
    assert "_rpc" not in result.stdout and "_worker" not in result.stdout
    assert "SUPPRESS" not in result.stdout
    for name in ("connect", "doctor", "prepare", "stage", "submit", "status",
                 "cancel", "collect", "archive", "logs", "restore"):
        line = next(line for line in result.stdout.splitlines() if line.split()[:1] == [name])
        assert len(line.split()) > 1, f"{name} has no description"
