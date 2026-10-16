"""Viewer behavior against artifacts captured from a real Lightcone 0.5 worker."""

import json
from pathlib import Path
import shutil
import sys

import pytest

from testlib import repo_path

pytest.importorskip("astra.validation")
sys.path.insert(0, str(repo_path("extensions/astra-viewer")))
from neurodesk_astra_view.graph import build_graph  # noqa: E402
from neurodesk_astra_view.manifest import load_run, RunManifestError  # noqa: E402

FIXTURE = repo_path("tests/fixtures/lightcone-0.5")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(FIXTURE / "project", root)
    return root


def graph(root, run, universe="baseline"):
    return build_graph(root / "astra.yaml", root / f"universes/{universe}.yaml", run)


@pytest.mark.parametrize(
    "state,status", [("current", "ok"), ("behind", "behind"), ("stale", "stale")]
)
def test_real_status_preserves_freshness_without_verification(project, state, status):
    result = graph(project, FIXTURE / f"{state}.json")
    assert not result["errors"]
    outputs = {
        node["id"].removeprefix("output:root/"): node
        for node in result["nodes"]
        if node["kind"] == "output"
    }
    assert outputs["answer"]["status"] == status
    assert outputs["pending"]["status"] == "not_run"
    assert result["trust"]["level"] == "executed-unverified"
    assert result["trust"]["output_integrity"] == "not-run"
    assert not outputs["answer"]["published"]


def test_unmaterialized_other_universe_and_missing_manifest_are_grey(project):
    assert graph(project, FIXTURE / "never.json")["trust"]["level"] == "spec-only"
    assert (
        graph(project, FIXTURE / "current.json", "alternative")["trust"]["level"]
        == "spec-only"
    )
    (project / "results/baseline/.answer.manifest.json").unlink()
    assert graph(project, FIXTURE / "current.json")["trust"]["level"] == "spec-only"
    assert (
        graph(project, project / "ro-crate-metadata.json")["trust"]["level"]
        == "spec-only"
    )


def test_crate_uses_relationships_and_hashes_actual_output(project):
    crate = project / "ro-crate-metadata.json"
    data = json.loads(crate.read_text())
    for entity in data["@graph"]:
        entity["name"] = "display names carry no identity"
    crate.write_text(json.dumps(data))
    result = graph(project, crate)
    assert not result["errors"]
    assert result["trust"]["level"] == "executed-unverified"
    overlay = load_run(crate, project_root=project, universe_id="baseline")
    record = overlay["records"]["answer"]
    assert Path(record["artifact_path"]).read_bytes() == b"42\n"
    assert record["hermeticity"]["fs"] == "declared"
    (project / "results/baseline/answer.txt").write_text("tampered")
    with pytest.raises(RunManifestError, match="stale hash"):
        load_run(crate, project_root=project, universe_id="baseline")


def test_crate_rejects_symlink_escape_and_accepts_confined_annex_shape(
    project, tmp_path
):
    artifact = project / "results/baseline/answer.txt"
    content = tmp_path / "object"
    content.write_bytes(artifact.read_bytes())
    artifact.unlink()
    artifact.symlink_to(content)
    result = graph(project, project / "ro-crate-metadata.json")
    assert "escapes" in str(result["errors"])
    artifact.unlink()
    local = project / ".git/annex/objects/content"
    local.parent.mkdir(parents=True)
    local.write_bytes(content.read_bytes())
    artifact.symlink_to(local)
    assert not graph(project, project / "ro-crate-metadata.json")["errors"]
    local.unlink()
    result = graph(project, project / "ro-crate-metadata.json")
    assert not result["errors"]
    assert result["trust"]["level"] == "executed-unverified"


@pytest.mark.parametrize(
    "mutation", ["duplicate", "traversal", "missing-universe", "bad-hash", "foreign"]
)
def test_status_boundary(project, mutation):
    data = json.loads((FIXTURE / "current.json").read_text())
    row = next(row for row in data["outputs"] if row["output"] == "baseline/answer")
    if mutation == "duplicate":
        data["outputs"].append(row.copy())
    elif mutation == "traversal":
        row["output"] = "baseline/../answer"
    elif mutation == "missing-universe":
        row["output"] = "answer"
    elif mutation == "bad-hash":
        row["data_version"] = "sha256:no"
    else:
        row["foreign_write"] = "f" * 40
    path = project / "snapshot.json"
    path.write_text(json.dumps(data))
    result = graph(project, path)
    if mutation == "foreign":
        assert not result["errors"]
        answer = next(n for n in result["nodes"] if n["id"] == "output:root/answer")
        assert answer["status"] == "stale"
        assert result["trust"]["output_integrity"] == "not-run"
    else:
        assert result["errors"]


def test_nested_ids_are_not_reduced_to_basename(project):
    data = json.loads((FIXTURE / "never.json").read_text())
    data["outputs"][0]["output"] = "baseline/sub/deeper/answer"
    path = project / "snapshot.json"
    path.write_text(json.dumps(data))
    overlay = load_run(path, project_root=project, universe_id="baseline")
    assert "sub/deeper/answer" in overlay["records"]
    assert overlay["trust"]["level"] == "spec-only"


def test_duplicate_crate_entities_and_manifest_tampering_fail_closed(project):
    crate = project / "ro-crate-metadata.json"
    data = json.loads(crate.read_text())
    data["@graph"].append(data["@graph"][0])
    crate.write_text(json.dumps(data))
    assert "duplicate" in str(graph(project, crate)["errors"])
    shutil.copyfile(FIXTURE / "project/ro-crate-metadata.json", crate)
    manifest = project / "results/baseline/.answer.manifest.json"
    data = json.loads(manifest.read_text())
    data["recipe"] = "fabricated recipe"
    manifest.write_text(json.dumps(data))
    assert "stale hash" in str(graph(project, crate)["errors"])


def test_module_attestation_remains_unverified(project):
    for name, pin in [
        ("module-manifest.json", "1.0"),
        ("module-refresh-manifest.json", "1.1"),
    ]:
        target = project / name
        shutil.copyfile(FIXTURE / name, target)
        overlay = load_run(target, project_root=project, universe_id="baseline")
        assert overlay["trust"]["level"] == "executed-unverified"
        assert overlay["trust"]["output_integrity"] == "not-run"
        record = overlay["records"]["answer"]
        assert record["hermeticity"]["fs"] == "open"
        assert record["hermeticity"]["modules"] == [f"neurodesk-fixture/{pin}"]


def test_unfetched_annex_pointer_is_never_previewed(project):
    artifact = project / "results/baseline/answer.txt"
    artifact.write_text("/annex/objects/SHA256E-s3--unfetched.txt\n")
    overlay = load_run(
        project / "ro-crate-metadata.json", project_root=project, universe_id="baseline"
    )
    assert overlay["trust"]["level"] == "executed-unverified"
    assert overlay["records"]["answer"]["artifact_path"] is None
