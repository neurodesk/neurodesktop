"""Real DataLad snapshots, collection and archive round trips using the installed job package.

`ssh`, Slurm and Apptainer are local stand-ins: the cluster side is the same
standard-library helper that runs on a real site.
"""

import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest


def test_installed_remote_jobs_cli():
    helper = Path("/usr/local/bin/neurodesk-jobs")
    assert helper.stat().st_uid == 0
    assert helper.stat().st_mode & 0o022 == 0
    result = subprocess.run([helper, "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "collect" in result.stdout and "archive" in result.stdout


def executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    executable(binaries / "ssh", f"""#!{sys.executable}
import os, sys
args = sys.argv[1:]
while args and args[0] in ("-o", "-O"):
    if args[0] == "-O":
        sys.exit(0)
    args = args[2:]
os.execvp("sh", ["sh", "-c", args[1]])
""")
    executable(binaries / "sbatch", f"""#!{sys.executable}
import os, pathlib, subprocess, sys
if sys.argv[1:] == ["--version"]:
    print("slurm fixture"); sys.exit(0)
if "--test-only" in sys.argv:
    sys.exit(0)
options = dict(arg[2:].split("=", 1) for arg in sys.argv[1:] if "=" in arg)
code = subprocess.run(["sh", "-c", sys.stdin.read()],
                      env=dict(os.environ, SLURM_JOB_ID="77", SLURM_RESTART_COUNT="0")).returncode
pathlib.Path({str(tmp_path / 'sacct.rows')!r}).write_text("|".join([
    "77", options["job-name"], options["comment"], "COMPLETED" if code == 0 else "FAILED",
    f"{{code}}:0", {getpass.getuser()!r}]) + "\\n")
print("77")
""")
    executable(binaries / "sacct", f"#!/bin/sh\ncat {tmp_path / 'sacct.rows'} 2>/dev/null || true\n")
    executable(binaries / "squeue", "#!/bin/sh\n")
    executable(binaries / "apptainer", f"""#!{sys.executable}
import subprocess, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("apptainer version fixture"); sys.exit(0)
binds, index = {{}}, 1
while args[index].startswith("--"):
    if args[index] == "--bind":
        source, target, _ = args[index + 1].split(":")
        binds[target] = source
    index += 2 if args[index] in ("--bind", "--pwd") else 1
command = [binds["/inputs"] + a[7:] if a.startswith("/inputs") else
           binds["/outputs"] + a[8:] if a.startswith("/outputs") else a
           for a in args[index + 1:]]
sys.exit(subprocess.run(command, cwd=binds["/outputs"]).returncode)
""")
    monkeypatch.setenv("PATH", str(binaries) + ":" + os.environ["PATH"])
    with tempfile.TemporaryDirectory(prefix="nj-home-", dir="/tmp") as home:
        monkeypatch.setattr(Path, "home", lambda: Path(home))
        yield tmp_path


def test_snapshot_job_collection_and_offline_restore(cluster, monkeypatch):
    from neurodesk_jobs import client, core, data

    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Job test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "job@test.invalid")
    tmp_path = cluster
    source = tmp_path / "source"
    data.create(source)
    core.run(["datalad", "create", "-d", source, source / "a"])
    (source / "a" / "numbers.bin").write_bytes(b"1\n2\n3\n")
    data.save(source / "a", "Inputs")
    (source / "analyze.sh").write_text("cat \"$1\" > \"$2\"\n")
    core.run(["datalad", "save", "-r", "-d", source, "-m", "Pinned input"])
    image = tmp_path / "image.sif"
    image.write_bytes(b"software artifact used only in this storage test")
    request = {"inputs": ["analyze.sh", "a/numbers.bin"],
               "command": ["sh", "/inputs/analyze.sh", "/inputs/a/numbers.bin", "/outputs/result.txt"],
               "validate": ["test", "-s", "/outputs/result.txt"], "outputs": ["result.txt"]}
    site = {"host": "fixture", "python": sys.executable, "path": os.environ["PATH"],
            "state_root": str(tmp_path / "journal"), "data_root": str(tmp_path / "scratch")}
    # A personal global gitignore must not drop snapshot inputs or results.
    (tmp_path / "global-ignore").write_text("*.bin\nresult.txt\n")
    (tmp_path / "gitconfig").write_text(f"[core]\n\texcludesFile = {tmp_path / 'global-ignore'}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    state = tmp_path / "client"
    cli = client.Client(site, state)
    run_id = cli.prepare(source, image, request)["run_id"]
    manifest = cli.record(run_id)["manifest"]
    assert len(manifest["sources"]) == 2
    assert cli.submit(run_id)["state"] == "accepted"
    collection = cli.collect(run_id)
    output = Path(collection["path"])
    assert (output / "result.txt").read_bytes() == b"1\n2\n3\n"
    assert core.git(output, "rev-parse", "HEAD") == collection["commit"]
    execution = json.loads((output / "execution.json").read_text())
    assert execution["manifest"] == manifest
    assert cli.collect(run_id) == collection
    monkeypatch.chdir(tmp_path)
    archive = cli.archive(run_id, Path("archives"))
    assert Path(archive["path"]).is_absolute()
    monkeypatch.chdir(source)
    assert cli.archive(run_id) == archive
    # The cluster copy is not needed to restore the archive.
    for path in (tmp_path / "scratch").rglob("*"):
        if not path.is_symlink():
            path.chmod(0o700 if path.is_dir() else 0o600)
    shutil.rmtree(tmp_path / "scratch")
    offline = dict(GIT_SSH_COMMAND="/bin/false", GIT_ALLOW_PROTOCOL="file")
    for key, value in offline.items():
        monkeypatch.setenv(key, value)
    restored = client.restore(archive["path"], tmp_path / "restored")
    assert restored["run_id"] == run_id
    assert (tmp_path / "restored/output/result.txt").read_bytes() == b"1\n2\n3\n"
    assert (tmp_path / "restored/input/data/a/numbers.bin").read_bytes() == b"1\n2\n3\n"
    assert core.git(tmp_path / "restored/output", "rev-parse", "HEAD") == collection["commit"]
