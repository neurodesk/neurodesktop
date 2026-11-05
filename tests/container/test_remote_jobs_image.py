"""Real DataLad RIA and archive round trips using the installed job package."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import shutil
import subprocess
import tarfile

import pytest


def test_installed_remote_jobs_cli():
    helper = Path("/usr/local/bin/neurodesk-jobs")
    assert helper.stat().st_uid == 0
    assert helper.stat().st_mode & 0o022 == 0
    result = subprocess.run([helper, "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "collect" in result.stdout and "archive" in result.stdout


def test_ria_nested_snapshot_publication_and_offline_restore(tmp_path, monkeypatch):
    from neurodesk_jobs import client, core, data

    monkeypatch.setenv("GIT_AUTHOR_NAME", "Job test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "job@test.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Job test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "job@test.invalid")
    source = tmp_path / "source"
    data.create(source)
    core.run(["datalad", "create", "-d", source, source / "a"])
    (source / "a" / "numbers.bin").write_bytes(b"1\n2\n3\n")
    data.save(source / "a", "Inputs")
    (source / "analyze.sh").write_text("cat /inputs/a/numbers.bin > /outputs/result.txt\n")
    core.run(["datalad", "save", "-r", "-d", source, "-m", "Pinned input"])
    image = tmp_path / "image.sif"
    image.write_bytes(b"software artifact used only in this storage test")
    spec = {"inputs": ["analyze.sh", "a/numbers.bin"],
            "command": ["sh", "/inputs/analyze.sh"],
            "validate": ["test", "-s", "/outputs/result.txt"], "outputs": ["result.txt"]}
    capsule = tmp_path / "capsule"
    manifest = data.capsule(source, image, spec, capsule)
    assert len(manifest["sources"]) == 2
    store = tmp_path / "store"
    data.publish(capsule, data.store_url(store))
    core.git(capsule, "remote", "remove", "site")
    data.publish(capsule, data.store_url(store))
    # Force every Git SSH/HTTP fallback to fail. RIA content uses local files.
    monkeypatch.setenv("GIT_SSH_COMMAND", "/bin/false")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    clone = data.checkout(data.store_url(store), data.identity(capsule),
                          core.git(capsule, "rev-parse", "HEAD"), tmp_path / "clone")
    core.verify(clone, manifest["files"])
    with pytest.raises(core.JobError):
        core.git(clone, "push", "origin", "HEAD:refs/heads/accidental")
    (clone / "accidental.bin").write_bytes(bytes(range(256)))
    core.git(clone, "annex", "add", "--force-large", "accidental.bin")
    with pytest.raises(core.JobError):
        core.git(clone, "annex", "copy", "--to", "read-store", "--", "accidental.bin")
    core.git(clone, "reset", "--hard", "HEAD")
    # Every result is a new dataset, not a branch or a clone of a previous run.
    def result_dataset(index):
        output = tmp_path / f"output-{index}"
        data.create(output)
        (output / "result.bin").write_bytes(bytes(range(256)) * 4)
        data.save(output, "Result")
        receipt = data.verified_publication(output, store, {"attempt": index})
        return output, receipt
    with ThreadPoolExecutor(max_workers=2) as pool:
        datasets = list(pool.map(result_dataset, range(2)))
    assert datasets[0][1]["dataset_id"] != datasets[1][1]["dataset_id"]
    with pytest.raises(core.JobError):
        core.git(datasets[0][0], "annex", "drop", "--numcopies=1", "result.bin")
    assert (datasets[0][0] / "result.bin").is_file()
    exported = tmp_path / "exported"
    exported.mkdir()
    metadata = {"run": {"run_id": "a" * 32}, "datasets": {}}
    for name, dataset in (("input", clone), ("output", datasets[0][0])):
        metadata["datasets"][name] = data.archive_payload(dataset, exported / name)
    core.atomic(exported / "archive.json", metadata)
    archive_path = tmp_path / "archive.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for item in exported.iterdir():
            archive.add(item, arcname=item.name)
    # Remove the site store before restoring, including read-only annex objects.
    for path in store.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o700 if path.is_dir() else 0o600)
    shutil.rmtree(store)
    client.restore(archive_path, tmp_path / "restored")
    assert (tmp_path / "restored/output/result.bin").read_bytes() == bytes(range(256)) * 4
    assert (tmp_path / "restored/input/data/a/numbers.bin").read_text() == "1\n2\n3\n"
    assert core.git(tmp_path / "restored/output", "rev-parse", "HEAD") == datasets[0][1]["commit"]
    assert "origin" not in core.git(tmp_path / "restored/output", "remote").splitlines()
