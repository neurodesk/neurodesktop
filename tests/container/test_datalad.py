import os
import subprocess


def test_datalad_saves_and_retrieves_annexed_content(tmp_path):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Neurodesktop test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Neurodesktop test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }

    def datalad(*args, cwd=tmp_path):
        result = subprocess.run(
            ["datalad", *args], cwd=cwd, env=env,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    source = tmp_path / "source"
    datalad("create", str(source))
    (source / "measurements.tsv").write_text("subject\tvalue\nsub-01\t42\n")
    datalad("save", "-m", "Save measurements", "measurements.tsv", cwd=source)

    clone = tmp_path / "clone"
    datalad("clone", str(source), str(clone))
    retrieved = clone / "measurements.tsv"
    assert not retrieved.exists(), "Annexed data should require retrieval after cloning"
    datalad("get", "measurements.tsv", cwd=clone)
    assert retrieved.is_file(), "DataLad get reported success without retrieving content"
    assert retrieved.read_text() == "subject\tvalue\nsub-01\t42\n"

    datalad("drop", "measurements.tsv", cwd=clone)
    assert not retrieved.exists(), "Dropping the clone must remove its content"
    assert (source / "measurements.tsv").read_text() == "subject\tvalue\nsub-01\t42\n"
    datalad("get", "measurements.tsv", cwd=clone)
    assert retrieved.is_file(), "DataLad get reported success without retrieving content"
    assert retrieved.read_text() == "subject\tvalue\nsub-01\t42\n"
