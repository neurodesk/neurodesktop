"""Contract for `neurodesk-astra-provenance`.

`astra validate` never compares a spec's `container:` with anything, so an
agent can pin the wrong version and see five clean validations. This helper is
the mechanism that catches it: `publish` records what actually ran beside each
artifact, and `check` fails when the specification disagrees with the record
or names no software at all.

The subject is executed rather than read, so the tests exercise the real
PATH resolution, version probing, and rename ordering.
"""

import json
import os
import stat
import subprocess
import sys

import pytest

from testlib import repo_path


SCRIPT = repo_path("config/agents/neurodesk-astra-provenance")


def run_provenance(*arguments, cwd=None, path_prefix=None):
    environment = dict(os.environ)
    if path_prefix:
        environment["PATH"] = f"{path_prefix}{os.pathsep}{environment['PATH']}"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=environment,
    )


def make_tool(directory, name, version_line, exit_code=0):
    """A stand-in CLI that answers `--version` the way a real tool would."""
    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / name
    tool.write_text(
        f'#!/bin/bash\necho "{version_line}"\nexit {exit_code}\n', encoding="utf-8"
    )
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return tool


def make_project(tmp_path, spec):
    project = tmp_path / "project"
    (project / "derivatives").mkdir(parents=True)
    (project / "astra.yaml").write_text(spec, encoding="utf-8")
    return project


ONE_OUTPUT = """id: demo
outputs:
  - id: brain
    type: data
    recipe:
      command: bash src/analysis_01_bet.sh {output}
      container: "neurodesk fsl/6.0.7.22"
"""


def publish_brain(tmp_path, project, version_line="faketool v6.0.7.22"):
    make_tool(tmp_path / "bin", "faketool", version_line)
    temporary = project / "derivatives" / "brain.nii.gz.tmp"
    temporary.write_text("artifact bytes", encoding="utf-8")
    return run_provenance(
        "publish",
        str(temporary),
        str(project / "derivatives" / "brain.nii.gz"),
        "--output-id",
        "brain",
        "--tool",
        "faketool",
        path_prefix=str(tmp_path / "bin"),
    )


def test_publish_renames_the_artifact_and_records_the_run(tmp_path):
    project = make_project(tmp_path, ONE_OUTPUT)

    result = publish_brain(tmp_path, project)

    assert result.returncode == 0, result.stderr
    artifact = project / "derivatives" / "brain.nii.gz"
    assert artifact.read_text(encoding="utf-8") == "artifact bytes"
    assert not (project / "derivatives" / "brain.nii.gz.tmp").exists()

    record = json.loads(
        (project / "derivatives" / "brain.nii.gz.prov.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["output_id"] == "brain"
    assert record["tool"]["versions"] == ["6.0.7.22"]
    assert record["tool"]["path"] == str(tmp_path / "bin" / "faketool")
    assert record["tool"]["version_command"] == "faketool --version"
    assert record["hostname"]
    assert record["recorded_at"].endswith("Z")


def test_publish_records_the_script_that_ran(tmp_path):
    project = make_project(tmp_path, ONE_OUTPUT)
    script = project / "src" / "analysis_01_bet.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/bash\nbet in out\n", encoding="utf-8")
    make_tool(tmp_path / "bin", "faketool", "faketool v6.0.7.22")
    temporary = project / "derivatives" / "brain.nii.gz.tmp"
    temporary.write_text("artifact bytes", encoding="utf-8")

    result = run_provenance(
        "publish",
        str(temporary),
        str(project / "derivatives" / "brain.nii.gz"),
        "--output-id",
        "brain",
        "--tool",
        "faketool",
        "--script",
        str(script),
        path_prefix=str(tmp_path / "bin"),
    )

    assert result.returncode == 0, result.stderr
    record = json.loads(
        (project / "derivatives" / "brain.nii.gz.prov.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["script"]["path"] == str(script)
    assert record["script"]["sha256"]


def test_publish_reads_the_version_from_a_tool_that_exits_non_zero(tmp_path):
    """FSL's `bet` prints its banner and usage, then fails."""
    project = make_project(tmp_path, ONE_OUTPUT)
    make_tool(tmp_path / "bin", "bet", "BET (Brain Extraction Tool) v2.1", exit_code=1)
    temporary = project / "derivatives" / "brain.nii.gz.tmp"
    temporary.write_text("artifact bytes", encoding="utf-8")

    result = run_provenance(
        "publish",
        str(temporary),
        str(project / "derivatives" / "brain.nii.gz"),
        "--output-id",
        "brain",
        "--tool",
        "bet",
        path_prefix=str(tmp_path / "bin"),
    )

    assert result.returncode == 0, result.stderr
    record = json.loads(
        (project / "derivatives" / "brain.nii.gz.prov.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["tool"]["versions"] == ["2.1"]
    assert record["tool"]["version_exit_code"] == 1


def test_publish_accepts_a_tool_that_spells_the_flag_differently(tmp_path):
    """dcm2niix answers `-v`, which is `--verbose` almost everywhere else."""
    project = make_project(tmp_path, ONE_OUTPUT)
    tool = tmp_path / "bin" / "dcm2niix"
    (tmp_path / "bin").mkdir(parents=True, exist_ok=True)
    tool.write_text(
        '#!/bin/bash\n'
        'if [ "$1" = "-v" ]; then echo "dcm2niiX version v1.0.20240202"; exit 0; fi\n'
        'echo "unknown option $1" >&2\nexit 1\n',
        encoding="utf-8",
    )
    tool.chmod(0o755)
    temporary = project / "derivatives" / "brain.nii.gz.tmp"
    temporary.write_text("artifact bytes", encoding="utf-8")

    result = run_provenance(
        "publish",
        str(temporary),
        str(project / "derivatives" / "brain.nii.gz"),
        "--output-id",
        "brain",
        "--tool",
        "dcm2niix",
        # The `=` form is required: argparse reads a bare `-v` as an option.
        "--version-flag=-v",
        path_prefix=str(tmp_path / "bin"),
    )

    assert result.returncode == 0, result.stderr
    record = json.loads(
        (project / "derivatives" / "brain.nii.gz.prov.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["tool"]["versions"] == ["1.0.20240202"]
    assert record["tool"]["version_command"] == "dcm2niix -v"


def test_publish_refuses_a_tool_it_cannot_question(tmp_path):
    """A sidecar must never claim provenance the helper did not observe."""
    project = make_project(tmp_path, ONE_OUTPUT)
    make_tool(tmp_path / "bin", "silent", "no version here")
    temporary = project / "derivatives" / "brain.nii.gz.tmp"
    temporary.write_text("artifact bytes", encoding="utf-8")

    result = run_provenance(
        "publish",
        str(temporary),
        str(project / "derivatives" / "brain.nii.gz"),
        "--output-id",
        "brain",
        "--tool",
        "silent",
        path_prefix=str(tmp_path / "bin"),
    )

    assert result.returncode != 0
    assert "Could not read a version" in result.stderr
    assert "--version-flag" in result.stderr
    assert not (project / "derivatives" / "brain.nii.gz").exists()
    assert not (project / "derivatives" / "brain.nii.gz.prov.json").exists()
    assert temporary.exists(), "a refused publish must leave the attempt intact"


def test_publish_names_a_missing_output_directory(tmp_path):
    project = make_project(tmp_path, ONE_OUTPUT)
    make_tool(tmp_path / "bin", "faketool", "faketool v6.0.7.22")
    temporary = project / "derivatives" / "brain.nii.gz.tmp"
    temporary.write_text("artifact bytes", encoding="utf-8")

    result = run_provenance(
        "publish",
        str(temporary),
        str(project / "missing" / "brain.nii.gz"),
        "--output-id",
        "brain",
        "--tool",
        "faketool",
        path_prefix=str(tmp_path / "bin"),
    )

    assert result.returncode != 0
    assert "No directory" in result.stderr
    assert temporary.exists()


def test_publish_refuses_to_overwrite_a_final_artifact(tmp_path):
    project = make_project(tmp_path, ONE_OUTPUT)
    final = project / "derivatives" / "brain.nii.gz"
    final.write_text("an earlier result", encoding="utf-8")

    result = publish_brain(tmp_path, project)

    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr
    assert final.read_text(encoding="utf-8") == "an earlier result"


def test_publish_replaces_only_when_asked(tmp_path):
    project = make_project(tmp_path, ONE_OUTPUT)
    final = project / "derivatives" / "brain.nii.gz"
    final.write_text("an earlier result", encoding="utf-8")
    make_tool(tmp_path / "bin", "faketool", "faketool v6.0.7.22")
    temporary = project / "derivatives" / "brain.nii.gz.tmp"
    temporary.write_text("artifact bytes", encoding="utf-8")

    result = run_provenance(
        "publish",
        str(temporary),
        str(final),
        "--output-id",
        "brain",
        "--tool",
        "faketool",
        "--replace",
        path_prefix=str(tmp_path / "bin"),
    )

    assert result.returncode == 0, result.stderr
    assert final.read_text(encoding="utf-8") == "artifact bytes"


def test_check_passes_when_the_spec_names_what_ran(tmp_path):
    pytest.importorskip("yaml")
    project = make_project(tmp_path, ONE_OUTPUT)
    assert publish_brain(tmp_path, project).returncode == 0

    result = run_provenance("check", str(project))

    assert result.returncode == 0, result.stderr + result.stdout
    assert "brain" in result.stdout


def test_check_catches_a_version_the_tool_never_reported(tmp_path):
    """The failure this helper exists for: a typed assertion nobody compared."""
    pytest.importorskip("yaml")
    project = make_project(tmp_path, ONE_OUTPUT)
    assert publish_brain(
        tmp_path, project, version_line="faketool v9.21.1"
    ).returncode == 0

    result = run_provenance("check", str(project))

    assert result.returncode == 1
    assert "neurodesk fsl/6.0.7.22" in result.stderr
    assert "9.21.1" in result.stderr


def test_check_does_not_accept_a_version_that_is_merely_a_substring(tmp_path):
    pytest.importorskip("yaml")
    project = make_project(
        tmp_path,
        ONE_OUTPUT.replace("neurodesk fsl/6.0.7.22", "neurodesk tool/12.5.10"),
    )
    assert publish_brain(tmp_path, project, version_line="faketool v2.5.1").returncode == 0

    result = run_provenance("check", str(project))

    assert result.returncode == 1
    assert "2.5.1" in result.stderr


def test_check_fails_a_recipe_that_names_no_software(tmp_path):
    """The root cause upstream validation misses entirely."""
    pytest.importorskip("yaml")
    project = make_project(
        tmp_path, ONE_OUTPUT.replace('      container: "neurodesk fsl/6.0.7.22"\n', "")
    )

    result = run_provenance("check", str(project))

    assert result.returncode == 1
    assert "names no software" in result.stderr


def test_check_fails_when_nothing_recorded_the_run(tmp_path):
    pytest.importorskip("yaml")
    project = make_project(tmp_path, ONE_OUTPUT)

    result = run_provenance("check", str(project))

    assert result.returncode == 1
    assert "nothing recorded what ran" in result.stderr


def test_check_inherits_the_analysis_container_and_skips_re_exports(tmp_path):
    pytest.importorskip("yaml")
    project = make_project(
        tmp_path,
        """id: demo
container: "neurodesk fsl/6.0.7.22"
outputs:
  - id: brain
    type: data
    recipe:
      command: bash src/analysis_01_bet.sh {output}
  - id: reused
    type: data
    from: other#brain
""",
    )
    assert publish_brain(tmp_path, project).returncode == 0

    result = run_provenance("check", str(project))

    assert result.returncode == 0, result.stderr + result.stdout
    assert "reused" not in result.stdout


def test_check_walks_sub_analyses(tmp_path):
    pytest.importorskip("yaml")
    project = make_project(
        tmp_path,
        """id: demo
analyses:
  extraction:
    id: extraction
    outputs:
      - id: nested
        type: data
        recipe:
          command: bash src/analysis_01_bet.sh {output}
""",
    )

    result = run_provenance("check", str(project))

    assert result.returncode == 1
    assert "nested: the specification names no software" in result.stderr


def test_the_helper_ships_on_path_in_the_image():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert (
        "source=config/agents/neurodesk-astra-provenance,"
        "target=/tmp/agents/neurodesk-astra-provenance,ro" in dockerfile
    )
    assert (
        "install -m 0755 -o root -g users /tmp/agents/neurodesk-astra-provenance "
        "/usr/local/bin/neurodesk-astra-provenance" in dockerfile
    )


def test_the_analysis_contract_directs_agents_to_the_helper():
    contract = repo_path("config/agents/AGENTS.md").read_text(encoding="utf-8")

    assert "neurodesk-astra-provenance publish" in contract
    assert "neurodesk-astra-provenance check" in contract
