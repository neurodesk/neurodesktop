"""Packaging contracts for Renovate's shared DOM harness pins."""

import json
import subprocess

import pytest

from testlib import repo_path


SURFACES = (
    ".github/workflows/unit-tests.yml",
    "docs/testing.md",
    "config/agentic/Dockerfile",
)
PACKAGES = {
    "jsdom", "typescript", "esbuild", "@jupyterlab/docregistry",
    "@jupyterlab/coreutils", "@jupyterlab/services", "@lumino/widgets",
}


def extract(config, path, content, datasource="npm"):
    # Execute the configured JavaScript regexes, including recursive matches.
    # This is a focused packaging check, not a replacement for Renovate validation.
    result = subprocess.run(
        ["node", "-e", r"""
const { config, path, content, datasource } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const deps = [];
for (const manager of config.customManagers) {
  if (manager.datasourceTemplate !== datasource ||
      !manager.managerFilePatterns.some(p => new RegExp(p.slice(1, -1)).test(path))) continue;
  let inputs = [content];
  for (const pattern of manager.matchStrings) {
    const matches = inputs.flatMap(input => [...input.matchAll(new RegExp(pattern, 'g'))]);
    for (const match of matches) {
      const { currentValue } = match.groups ?? {};
      const depName = match.groups?.depName ?? manager.depNameTemplate;
      if (depName && currentValue) deps.push([depName, currentValue]);
    }
    if (manager.matchStringsStrategy === 'recursive') inputs = matches.map(m => m[0]);
  }
}
process.stdout.write(JSON.stringify(deps));
"""],
        input=json.dumps({"config": config, "path": path, "content": content, "datasource": datasource}),
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("path", SURFACES)
def test_renovate_extracts_all_dom_pins_at_the_same_versions(path):
    config = json.loads(repo_path("renovate.json").read_text())
    reference = extract(config, SURFACES[0], repo_path(SURFACES[0]).read_text())
    actual = extract(config, path, repo_path(path).read_text())

    assert {name for name, _ in reference} == PACKAGES
    assert len(actual) == len(PACKAGES), "Each DOM pin must be extracted exactly once"
    assert sorted(actual) == sorted(reference), f"DOM harness dependency drift: {path}"


@pytest.mark.parametrize("path", SURFACES)
def test_renovate_groups_dom_pins_without_absorbing_other_worker_dependencies(path):
    config = json.loads(repo_path("renovate.json").read_text())
    rule = next(r for r in config["packageRules"] if r.get("groupName") == "unit test DOM harness")

    assert path in rule["matchFileNames"]
    assert rule["matchDatasources"] == ["npm"]
    assert set(rule["matchDepNames"]) == PACKAGES
    assert "@openai/codex" not in rule["matchDepNames"]
    assert any("aligned with the JupyterLab version in the image" in note for note in rule["prBodyNotes"])


@pytest.mark.parametrize("path", SURFACES[1:])
@pytest.mark.parametrize("replacement", ["", "typescript@99.0.0"])
def test_dom_parity_rejects_missing_or_stale_pins(path, replacement, tmp_path, monkeypatch):
    config = json.loads(repo_path("renovate.json").read_text())
    pins = dict(extract(config, path, repo_path(path).read_text()))
    original = repo_path(path).read_text()
    changed = tmp_path / "changed"
    changed.write_text(original.replace(f"typescript@{pins['typescript']}", replacement))
    original_repo_path = repo_path
    monkeypatch.setitem(
        globals(), "repo_path", lambda relative: changed if relative == path else original_repo_path(relative)
    )

    with pytest.raises(AssertionError):
        test_renovate_extracts_all_dom_pins_at_the_same_versions(path)
