import json
import subprocess
import sys
from pathlib import Path

from testlib import repo_path


SCRIPT = repo_path("scripts/audit_image_versions.py")


def run_audit(dockerfile: Path, fixtures: Path):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dockerfile",
            str(dockerfile),
            "--offline-fixtures",
            str(fixtures),
            "--format",
            "json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_offline_audit_reports_latest_upstream_and_latest_compatible(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        """\
ARG BASE_IMAGE_TAG=2026-09-07
FROM quay.io/jupyter/base-notebook:${BASE_IMAGE_TAG}
ARG JUPYTER_AI_VERSION=3.1.2
RUN pip install jupyter_ai==${JUPYTER_AI_VERSION} "mcp>=1.28.1,<2"
""",
        encoding="utf-8",
    )
    fixtures = tmp_path / "releases.json"
    fixtures.write_text(
        json.dumps(
            {
                "oci:quay.io/jupyter/base-notebook": ["2026-09-07"],
                "pypi:jupyter-ai": ["3.1.2", "3.2.0"],
                "pypi:mcp": ["1.28.1", "1.30.0", "2.2.0"],
            }
        ),
        encoding="utf-8",
    )

    completed = run_audit(dockerfile, fixtures)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    by_key = {row["key"]: row for row in report["dependencies"]}
    assert by_key["pypi:jupyter-ai"]["status"] == "update-available"
    assert by_key["pypi:mcp"] == {
        "current": ">=1.28.1,<2",
        "key": "pypi:mcp",
        "latest_compatible": "1.30.0",
        "latest_upstream": "2.2.0",
        "source": f"{dockerfile}:4",
        "status": "compatible-update-available",
        "why": "Notebook Intelligence still imports MCP's v1 FastMCP API.",
    }


def test_offline_mode_never_falls_back_to_network(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("ARG UV_VERSION=0.12.3\n", encoding="utf-8")
    fixtures = tmp_path / "releases.json"
    fixtures.write_text("{}\n", encoding="utf-8")

    completed = run_audit(dockerfile, fixtures)

    assert completed.returncode == 2
    report = json.loads(completed.stdout)
    assert report["errors"] == ["missing offline releases for pypi:uv"]


def test_user_facing_package_wins_over_infrastructure_dependency(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        """\
ARG SNAKEMAKE_VERSION=9.26.1
RUN pip install snakemake==${SNAKEMAKE_VERSION} "packaging==25.0"
""",
        encoding="utf-8",
    )
    fixtures = tmp_path / "releases.json"
    fixtures.write_text(
        json.dumps(
            {
                "pypi:snakemake": ["9.15.0", "9.26.1"],
                "pypi:packaging": ["25.0", "26.3"],
            }
        ),
        encoding="utf-8",
    )

    completed = run_audit(dockerfile, fixtures)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    by_key = {row["key"]: row for row in report["dependencies"]}
    assert by_key["pypi:snakemake"]["status"] == "current"
    assert by_key["pypi:packaging"]["status"] == "held"
    assert by_key["pypi:packaging"]["latest_compatible"] == "25.0"


def test_new_version_argument_fails_closed(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("ARG SURPRISE_VERSION=1.2.3\n", encoding="utf-8")
    fixtures = tmp_path / "releases.json"
    fixtures.write_text("{}\n", encoding="utf-8")

    completed = run_audit(dockerfile, fixtures)

    assert completed.returncode == 2
    report = json.loads(completed.stdout)
    assert report["errors"] == [
        f"untracked direct version declaration ARG SURPRISE_VERSION at {dockerfile}:1"
    ]


def test_root_dockerfile_has_no_untracked_supported_declarations(tmp_path):
    fixtures = tmp_path / "releases.json"
    fixtures.write_text(
        json.dumps({key: ["0"] for key in _catalog_keys()}), encoding="utf-8"
    )

    completed = run_audit(repo_path("Dockerfile"), fixtures)

    report = json.loads(completed.stdout)
    assert not [error for error in report["errors"] if error.startswith("untracked")]


def _catalog_keys() -> list[str]:
    namespace = {}
    exec(SCRIPT.read_text(encoding="utf-8"), namespace)
    return sorted({entry.key for entry in namespace["CATALOG"]})
