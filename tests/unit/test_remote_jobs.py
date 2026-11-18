"""Exercise site discovery, staging, the worker, submission crash windows and archives."""

import ast
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

import pytest
from testlib import repo_path

PACKAGE = repo_path("config/remote_jobs")


@pytest.fixture
def jobs(monkeypatch):
    monkeypatch.syspath_prepend(str(PACKAGE))
    from neurodesk_jobs import client, core, remote, transport
    return client, core, remote, transport


@pytest.fixture
def site(tmp_path):
    return {"host": "fixture", "python": sys.executable, "path": os.environ["PATH"],
            "state_root": str(tmp_path / "journal"), "data_root": str(tmp_path / "data")}


@pytest.fixture
def short_home(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="nj-home-", dir="/tmp") as home:
        monkeypatch.setattr(Path, "home", lambda: Path(home))
        yield Path(home)


def executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    """Local stand-ins for ssh, Slurm and Apptainer on PATH.

    Request it before `site`, whose cluster PATH is captured when it is built.

    `ssh` runs the remote command locally, `sbatch` runs the job script at once
    inside a fake allocation, and `apptainer exec` runs the command with the
    /inputs and /outputs binds substituted.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir()
    executable(binaries / "ssh", f"""#!{sys.executable}
import os, sys
args = sys.argv[1:]
while args and args[0] in ("-o", "-O"):
    if args[0] == "-O":
        sys.exit(0)
    args = args[2:]
host, command = args[0], args[1]
os.execvp("sh", ["sh", "-c", command])
""")
    executable(binaries / "sbatch", f"""#!{sys.executable}
import getpass, os, pathlib, subprocess, sys
if sys.argv[1:] == ["--version"]:
    print("slurm fixture"); sys.exit(0)
if "--test-only" in sys.argv:
    sys.stdin.read()
    if os.environ.get("REJECT_OPTIONS"):
        sys.exit("sbatch: error: invalid partition specified: nope")
    sys.exit(0)
pathlib.Path({str(tmp_path / 'sbatch.argv')!r}).write_text("\\n".join(sys.argv[1:]))
options = dict(arg[2:].split("=", 1) for arg in sys.argv[1:] if "=" in arg)
script = sys.stdin.read()
env = dict(os.environ, SLURM_JOB_ID="1234", SLURM_RESTART_COUNT="0", APPTAINER_BINDPATH="/scratch")
code = subprocess.run(["sh", "-c", script], env=env).returncode
row = "|".join(["1234", options["job-name"], options["comment"],
                "COMPLETED" if code == 0 else "FAILED", f"{{code}}:0", getpass.getuser()])
pathlib.Path({str(tmp_path / 'sacct.rows')!r}).write_text(row + "\\n")
print("1234")
""")
    executable(binaries / "sacct", f"""#!/bin/sh
cat {tmp_path / 'sacct.rows'} 2>/dev/null || true
""")
    executable(binaries / "squeue", "#!/bin/sh\n")
    executable(binaries / "apptainer", f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("apptainer version fixture"); sys.exit(0)
pathlib.Path({str(tmp_path / 'container.env')!r}).write_text(json.dumps(dict(os.environ)))
binds, index = {{}}, 1
while args[index].startswith("--"):
    if args[index] in ("--bind", "--pwd"):
        if args[index] == "--bind":
            source, target, _ = args[index + 1].split(":")
            binds[target] = source
        index += 2
    else:
        index += 1
command = [binds["/inputs"] + a[7:] if a.startswith("/inputs") else
           binds["/outputs"] + a[8:] if a.startswith("/outputs") else a
           for a in args[index + 1:]]
sys.exit(subprocess.run(command, cwd=binds["/outputs"]).returncode)
""")
    monkeypatch.setenv("PATH", str(binaries) + ":" + os.environ["PATH"])
    return binaries


def snapshot(root, files, request):
    """A prepared run's input directory and manifest, without DataLad."""
    root.mkdir(parents=True)
    entries = {}
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        entries[name] = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
    return {"schema": 1, "request": request, "sources": [], "executables": [],
            "image_sha256": entries["runtime.sif"]["sha256"], "files": entries}


ANALYSIS = {"inputs": ["numbers.txt", "mean.py"],
            "command": [sys.executable, "/inputs/mean.py", "/inputs/numbers.txt", "/outputs/mean.txt"],
            "validate": ["test", "-s", "/outputs/mean.txt"], "outputs": ["mean.txt"],
            "slurm": {"time": "00:05:00"}}
MEAN = (b"import sys\nvalues = [float(v) for v in open(sys.argv[1])]\n"
        b"open(sys.argv[2], 'w').write(str(sum(values) / len(values)))\n")


def prepared_run(client_module, core, site, state, request=ANALYSIS, numbers=b"2\n4\n6\n"):
    cli = client_module.Client(site, state)
    run_id = "c" * 32
    directory = cli.directory(run_id)
    manifest = snapshot(directory / "input", {
        "runtime.sif": b"image bytes", "data/numbers.txt": numbers, "data/mean.py": MEAN,
        "sources/0.bundle": b"never staged"}, request)
    core.atomic(directory / "run.json", {"run_id": run_id, "site": site, "state": "prepared",
                                         "request_digest": core.digest(manifest),
                                         "manifest": manifest})
    return cli, run_id


def test_staged_run_executes_and_results_download_verified(jobs, cluster, site, tmp_path, short_home):
    client, core, _, _ = jobs
    cli, run_id = prepared_run(client, core, site, tmp_path / "client")
    reply = cli.submit(run_id)  # stages automatically
    assert reply["state"] == "accepted" and reply["job_id"] == "1234"
    # Only the image and selected inputs are uploaded, into a checksummed store.
    objects = sorted(p.name for p in (tmp_path / "data/objects").rglob("*") if p.is_file())
    manifest = cli.record(run_id)["manifest"]
    assert objects == sorted(manifest["files"][n]["sha256"]
                             for n in ("runtime.sif", "data/numbers.txt", "data/mean.py"))
    argv = (tmp_path / "sbatch.argv").read_text().splitlines()
    assert "--time=00:05:00" in argv and "--mem=4G" in argv and "--cpus-per-task=1" in argv
    assert not any(arg.startswith(("--account", "--partition")) for arg in argv)
    # Site-wide bind defaults are not passed to the container.
    assert "APPTAINER_BINDPATH" not in json.loads((tmp_path / "container.env").read_text())
    observed = cli.status(run_id)
    assert observed["scheduler"]["state"] == "COMPLETED"
    attempt = observed["attempts"][0]
    assert attempt["state"] == "site-results-available"
    receipt = attempt["receipt"]
    assert set(receipt["files"]) == {"mean.txt", "execution.json", "outcome.json", "worker.log"}
    execution = json.loads((tmp_path / "data/work" / attempt["attempt_id"] / "output/execution.json").read_text())
    assert execution["tools"]["container"] == "apptainer version fixture"
    target = tmp_path / "mean.txt"
    cli.transport.download(site, cli.record(run_id)["helper"], {
        "site": site, "run_id": run_id, "attempt_id": attempt["attempt_id"], "path": "mean.txt"}, target)
    assert target.read_text() == "4.0"
    for name, message in (("other.txt", "not a published result"),
                          ("../request.json", "relative data file path")):
        with pytest.raises(core.JobError, match=message):
            cli.transport.download(site, cli.record(run_id)["helper"], {
                "site": site, "run_id": run_id, "attempt_id": attempt["attempt_id"],
                "path": name}, tmp_path / name.replace("/", "_"))


def test_restaging_skips_uploaded_objects(jobs, cluster, site, tmp_path, short_home, monkeypatch):
    client, core, _, _ = jobs
    cli, run_id = prepared_run(client, core, site, tmp_path / "client")
    cli.stage(run_id)
    uploads = []
    monkeypatch.setattr(cli.transport, "upload", lambda *args: uploads.append(args))
    cli.stage(run_id)
    assert uploads == []


def test_missing_compute_runtime_stops_before_analysis(jobs, cluster, site, tmp_path, short_home):
    client, core, _, _ = jobs
    (cluster / "apptainer").unlink()
    path = ":".join(p for p in os.environ["PATH"].split(":")
                    if not (Path(p) / "apptainer").exists() and not (Path(p) / "singularity").exists())
    site = dict(site, path=str(cluster) + ":" + path)
    cli, run_id = prepared_run(client, core, site, tmp_path / "client")
    cli.submit(run_id)
    attempt = cli.status(run_id)["attempts"][0]
    assert attempt["state"] == "failed" and "analysis_exit_code" not in attempt
    assert "Neither apptainer nor singularity" in attempt["error"]
    assert "compute node" in attempt["error"] and "site.json" in attempt["error"]


def test_truncated_object_is_uploaded_again(jobs, cluster, site, tmp_path, short_home):
    client, core, remote, _ = jobs
    cli, run_id = prepared_run(client, core, site, tmp_path / "client")
    cli.stage(run_id)
    entry = cli.record(run_id)["manifest"]["files"]["runtime.sif"]
    stored = remote.object_path(site["data_root"], entry["sha256"])
    stored.chmod(0o600)
    stored.write_bytes(b"image")  # an interrupted copy
    cli.stage(run_id)
    assert stored.read_bytes() == b"image bytes"


class BoundedStdin:
    """Fails if a reader asks for an unbounded read."""

    def __init__(self, data):
        self.buffer = self
        self.stream = io.BytesIO(data)

    def read(self, size=-1):
        assert 0 < size <= 8 * 1024 * 1024, "unbounded read"
        return self.stream.read(size)


def test_existing_object_drains_upload_in_bounded_chunks(jobs, tmp_path, monkeypatch):
    _, _, remote, _ = jobs
    content = b"payload"
    sha = hashlib.sha256(content).hexdigest()
    target = remote.object_path(str(tmp_path), sha)
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    monkeypatch.setattr(sys, "stdin", BoundedStdin(content))
    remote.put(str(tmp_path), sha, len(content))
    assert target.read_bytes() == content


def test_corrupted_staged_input_stops_before_analysis(jobs, cluster, site, tmp_path, short_home):
    client, core, remote, _ = jobs
    cli, run_id = prepared_run(client, core, site, tmp_path / "client")
    cli.stage(run_id)
    entry = cli.record(run_id)["manifest"]["files"]["data/numbers.txt"]
    stored = remote.object_path(site["data_root"], entry["sha256"])
    stored.chmod(0o600)
    stored.write_bytes(b"9\n9\n9\n")
    cli.submit(run_id)
    attempt = cli.status(run_id)["attempts"][0]
    assert attempt["state"] == "inputs-missing" and "checksum mismatch" in attempt["error"]
    assert "delete it there" in attempt["error"]
    assert "analysis_exit_code" not in attempt


def test_failed_validation_is_not_published(jobs, cluster, site, tmp_path, short_home):
    client, core, _, _ = jobs
    request = dict(ANALYSIS, validate=["false"])
    cli, run_id = prepared_run(client, core, site, tmp_path / "client", request=request)
    cli.submit(run_id)
    attempt = cli.status(run_id)["attempts"][0]
    assert attempt["state"] == "failed" and "receipt" not in attempt
    assert attempt["validation_exit_code"] != 0


def test_upload_rejects_incomplete_or_corrupted_bytes(jobs, tmp_path, monkeypatch):
    _, core, remote, _ = jobs
    content = b"payload"
    sha = hashlib.sha256(content).hexdigest()
    for data, size in ((content[:3], len(content)), (b"PAYLOAD", len(content))):
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
        with pytest.raises(core.JobError, match="incomplete or corrupted"):
            remote.put(str(tmp_path), sha, size)
    assert not remote.object_path(str(tmp_path), sha).exists()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(content)))
    remote.put(str(tmp_path), sha, len(content))
    assert remote.object_path(str(tmp_path), sha).read_bytes() == content


def test_connect_discovers_site_and_reuses_it(jobs, tmp_path, cluster, short_home, monkeypatch):
    client, core, _, _ = jobs
    home = tmp_path / "remote-home"
    scratch = tmp_path / "scratch"
    home.mkdir()
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCRATCH", str(scratch))
    state = tmp_path / "client"
    result = client.connect(state, "bunya")
    assert result["state"] == "connected"
    saved = core.read(state / "site.json")
    assert saved["host"] == "bunya"
    assert saved["state_root"] == f"{home}/.neurodesk-jobs"
    assert saved["data_root"] == f"{scratch}/neurodesk-jobs"
    assert Path(saved["python"]).is_absolute()
    assert result["checks"]["apptainer"] == "apptainer version fixture"
    # Reconnecting without a host keeps the saved site exactly.
    monkeypatch.setenv("SCRATCH", str(tmp_path))
    assert client.connect(state)["site"] == saved
    other = tmp_path / "other"
    other.mkdir()
    assert client.connect(state, "bunya", str(other))["site"]["data_root"] == str(other)


def test_connect_without_scratch_names_the_fix(jobs, tmp_path, cluster, short_home, monkeypatch):
    client, core, _, _ = jobs
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SCRATCH", str(tmp_path / "absent"))
    monkeypatch.setenv("USER", "no-such-user-nj")
    with pytest.raises(core.JobError, match="--data-root"):
        client.connect(tmp_path / "client", "cluster")


def test_validation_errors_name_the_field_and_the_fix(jobs, site):
    _, core, _, _ = jobs
    with pytest.raises(core.JobError, match=r"apptainer.*neurodesk jobs connect fixture"):
        core.site(dict(site, apptainer="/REPLACE/apptainer"), "site.json")
    with pytest.raises(core.JobError, match=r"site.json: data_root='relative' must be an absolute"):
        core.site(dict(site, data_root="relative"), "site.json")
    with pytest.raises(core.JobError, match="alias from ~/.ssh/config"):
        core.host("user@bunya")
    with pytest.raises(core.JobError, match="slurm may only set"):
        core.request(dict(ANALYSIS, slurm={"exclusive": "yes"}))
    for key, name in (("outputs", "sub/.gitattributes"), ("inputs", "code/.gitignore")):
        with pytest.raises(core.JobError, match="Git metadata"):
            core.request(dict(ANALYSIS, **{key: [name]}))
    with pytest.raises(core.JobError, match="reserved"):
        core.request(dict(ANALYSIS, outputs=["worker.log"]))


def test_commands_before_connect_explain_how_to_start(tmp_path):
    result = subprocess.run([sys.executable, "-m", "neurodesk_jobs.cli", "--state",
                             str(tmp_path), "prepare", "--dataset", "d", "--image", "i",
                             "--request", "r"],
                            env=dict(os.environ, PYTHONPATH=str(PACKAGE)),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "neurodesk jobs connect HOST" in result.stderr


def test_expired_connection_says_to_reconnect(jobs, site, tmp_path, short_home, monkeypatch):
    _, core, _, transport = jobs
    binaries = tmp_path / "bin"
    binaries.mkdir()
    executable(binaries / "ssh", "#!/bin/sh\nexit 255\n")
    monkeypatch.setenv("PATH", str(binaries) + ":" + os.environ["PATH"])
    with pytest.raises(core.JobError, match="neurodesk jobs connect fixture"):
        transport.SSH("fixture").command(["true"])


def test_cluster_helper_parses_as_python_39():
    # Bunya's system Python is 3.9; the helper runs there as a zipapp.
    for path in (PACKAGE / "neurodesk_jobs").glob("*.py"):
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 9))


def test_rejected_slurm_options_submit_nothing_and_say_what_to_fix(jobs, cluster, site, tmp_path, short_home, monkeypatch):
    client, core, remote, _ = jobs
    request = dict(ANALYSIS, slurm={"partition": "nope"})
    cli, run_id = prepared_run(client, core, site, tmp_path / "client", request=request)
    monkeypatch.setenv("REJECT_OPTIONS", "1")
    with pytest.raises(core.JobError, match=r"(?s)invalid partition.*slurm section.*submit again") as error:
        cli.submit(run_id)
    assert str(error.value).startswith("Slurm's pre-check failed")
    assert not (remote.run_directory(site, run_id) / "submit.claim").exists()
    assert not (tmp_path / "sbatch.argv").exists()


def test_unconfirmed_submission_fails_loudly(jobs, cluster, site, tmp_path, short_home, monkeypatch):
    client, core, _, _ = jobs
    cli, run_id = prepared_run(client, core, site, tmp_path / "client")
    cli.stage(run_id)
    monkeypatch.setattr(cli, "rpc", lambda record, operation, value, timeout=300: {
        "state": "submission-unknown", "error": "sbatch timed out"})
    with pytest.raises(core.JobError, match=r"(?s)sbatch timed out.*never submitted twice"):
        cli.submit(run_id)
    assert cli.record(run_id)["state"] == "submission-unknown"


@pytest.mark.parametrize("name", [".git/config", "nested/.gitattributes", ".gitignore"])
def test_uncollectable_outputs_are_not_published(jobs, cluster, site, tmp_path, short_home, name):
    client, core, _, _ = jobs
    writer = (b"import os, sys\nos.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True)\n"
              b"open(sys.argv[1], 'w').write('x')\nopen(sys.argv[2], 'w').write('4.0')\n")
    request = dict(ANALYSIS, command=[sys.executable, "/inputs/mean.py", f"/outputs/{name}",
                                      "/outputs/mean.txt"])
    cli, run_id = prepared_run(client, core, site, tmp_path / "client", request=request)
    record = cli.record(run_id)
    path = cli.directory(run_id) / "input/data/mean.py"
    path.write_bytes(writer)
    record["manifest"]["files"]["data/mean.py"] = {
        "sha256": hashlib.sha256(writer).hexdigest(), "size": len(writer)}
    record["request_digest"] = core.digest(record["manifest"])
    core.atomic(cli.directory(run_id) / "run.json", record)
    cli.submit(run_id)
    attempt = cli.status(run_id)["attempts"][0]
    assert attempt["state"] == "failed" and "receipt" not in attempt


def test_helper_upgrade_can_restage_an_unsubmitted_run(scheduler):
    value, accepted, _, remote = scheduler
    assert remote.prepare(dict(value, helper="/upgraded-helper.pyz")) == {"prepared": True}
    assert not accepted.exists()


@pytest.fixture
def scheduler(jobs, site, tmp_path, monkeypatch):
    _, core, remote, _ = jobs
    binaries = tmp_path / "bin"
    binaries.mkdir()
    accepted = tmp_path / "accepted"
    executable(binaries / "sbatch", f"""#!{sys.executable}
import os, pathlib, sys, time
if "--test-only" in sys.argv:
    sys.exit(0)
pathlib.Path({str(accepted)!r}).open('a').write('accepted\\n')
time.sleep(0.1)
sys.stdin.read()
if os.environ.get('LOSE_RESPONSE'):
    sys.exit(1)
print('1234')
""")
    for tool in ("squeue", "sacct"):
        executable(binaries / tool, f"#!{sys.executable}\nimport os\nprint(os.environ.get('{tool.upper()}', ''))\n")
    monkeypatch.setenv("PATH", str(binaries) + ":" + os.environ["PATH"])
    manifest = snapshot(tmp_path / "input", {"runtime.sif": b"image"}, ANALYSIS)
    entry = manifest["files"]["runtime.sif"]
    stored = remote.object_path(site["data_root"], entry["sha256"])
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"image")
    value = {"site": site, "run_id": "1" * 32, "request_digest": core.digest(manifest),
             "manifest": manifest, "helper": "/helper.pyz"}
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
    value, accepted, core, remote = scheduler
    with pytest.raises(core.JobError, match="different request"):
        remote.prepare(dict(value, site=dict(value["site"], python="/other/python3")))
    with pytest.raises(core.JobError, match="does not match its digest"):
        remote.prepare(dict(value, request_digest="f" * 64))
    with pytest.raises(core.JobError, match="does not match"):
        remote.submit(dict(value, request_digest="f" * 64))
    assert not accepted.exists()


def test_prepare_requires_staged_objects(scheduler):
    value, _, core, remote = scheduler
    manifest = dict(value["manifest"], files={"runtime.sif": {"sha256": "e" * 64, "size": 1}})
    with pytest.raises(core.JobError, match="run stage again"):
        remote.prepare(dict(value, run_id="2" * 32, manifest=manifest,
                            request_digest=core.digest(manifest)))


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
    sacct = Path(os.environ["PATH"].split(":")[0]) / "sacct"
    sacct.write_text(f"""#!{sys.executable}
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


def test_transport_is_noninteractive_and_private(jobs, short_home):
    _, _, _, transport = jobs
    ssh = transport.SSH("fixture")
    assert "BatchMode=yes" in ssh.options
    assert "ForwardAgent=no" in ssh.options
    assert ssh.sockets.stat().st_mode & 0o077 == 0


def test_connect_does_not_require_confirmation_for_each_reused_session(jobs, short_home, monkeypatch):
    _, _, _, transport = jobs
    ssh = transport.SSH("fixture")
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
    assert "controlpersist 14400\n" in configurations[0]


def test_cli_help_runs_without_site_or_connection():
    result = subprocess.run([sys.executable, "-m", "neurodesk_jobs.cli", "--help"],
                            env=dict(os.environ, PYTHONPATH=str(PACKAGE)),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "prepare" in result.stdout and "archive" in result.stdout
    assert "connect HOST" in result.stdout


def test_image_packages_cli_without_site_templates():
    dockerfile = repo_path("Dockerfile").read_text()
    assert "source=config/remote_jobs,target=/tmp/remote_jobs,ro" in dockerfile
    assert "pip install --no-deps /tmp/neurodesk-jobs-build" in dockerfile
    assert "/usr/local/bin/neurodesk-jobs" in dockerfile
    assert "remote-jobs/profiles" not in dockerfile
    assert not (PACKAGE / "profiles").exists()


def test_help_describes_public_commands_and_hides_private_entry_points():
    result = subprocess.run([sys.executable, "-m", "neurodesk_jobs.cli", "--help"],
                            cwd=PACKAGE, capture_output=True, text=True, check=True)
    assert result.stdout.startswith("usage: neurodesk jobs ")
    for private in ("_rpc", "_worker", "_put", "_get"):
        assert private not in result.stdout
    assert "SUPPRESS" not in result.stdout
    for name in ("connect", "doctor", "prepare", "stage", "submit", "status",
                 "cancel", "collect", "archive", "logs", "restore"):
        line = next(line for line in result.stdout.splitlines() if line.split()[:1] == [name])
        assert len(line.split()) > 1, f"{name} has no description"
