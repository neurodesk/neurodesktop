"""Exercise agent task boundaries and publication against real Git repositories."""

from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from testlib import load_source_module, repo_path


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("AGENTIC_CI_TOKEN", raising=False)
    monkeypatch.delenv("AGENTIC_PUSH_TOKEN", raising=False)
    return load_source_module(
        "agentic_worker_test", "/opt/agentic_worker.py", ".github/scripts/agentic_worker.py"
    )


@pytest.fixture
def github(worker, monkeypatch):
    responses = {
        "repos/owner/repo": {"default_branch": "main", "owner": {"login": "owner"}},
        "repos/owner/repo/commits/main": {"sha": "a" * 40},
        "repos/owner/repo/pulls?state=open&per_page=100": [],
        "repos/owner/repo/issues/42": {
            "number": 42, "state": "open", "title": "Bug", "body": "Broken",
            "labels": [],
        },
        "repos/owner/repo/issues/42/comments?per_page=100": [],
    }
    calls = []

    def api(path, payload=None):
        calls.append((path, payload))
        if path.startswith("repos/owner/repo/pulls?state=closed&head="):
            return responses.get("closed", [])
        if path not in responses:
            raise AssertionError(f"Unexpected GitHub request: {path}")
        return responses[path]

    monkeypatch.setattr(worker, "api", api)
    monkeypatch.setattr(worker, "pages", api)
    return responses, calls


@pytest.mark.parametrize("script", ["agentic_worker.py", "check_agentic_sandbox.py"])
def test_runner_host_scripts_load_without_python_311_datetime(script, tmp_path):
    # The self-hosted runner host executes these with Ubuntu 22.04's Python 3.10.
    (tmp_path / "sitecustomize.py").write_text("import datetime\ndel datetime.UTC\n")
    path = Path(".github/scripts") / script
    loader = (
        "import importlib.util, sys; "
        "spec = importlib.util.spec_from_file_location('host_script', sys.argv[1]); "
        "spec.loader.exec_module(importlib.util.module_from_spec(spec))"
    )
    subprocess.run(
        [sys.executable, "-c", loader, str(path.resolve())],
        cwd=tmp_path, env={"PYTHONPATH": str(tmp_path)}, check=True,
    )


def pull(**changes):
    value = {
        "number": 91, "state": "open", "title": "Fix", "body": "",
        "user": {"login": "github-actions[bot]"},
        "labels": [{"name": "agentic-workflow"}],
        "head": {
            "ref": "agentic/issue-42-100", "sha": "b" * 40,
            "repo": {"full_name": "owner/repo"},
        },
    }
    value.update(changes)
    return value


def test_prepare_issue_collects_current_context(worker, github, tmp_path):
    responses, _ = github
    responses["repos/owner/repo/issues/42/comments?per_page=100"] = [{"body": "Reproducer"}]
    task = worker.prepare("issue", "42", "owner/repo", "100", tmp_path)
    assert not task["skip"]
    assert task["branch"] == "agentic/issue-42-100"
    assert task["context"]["comments"] == [{"body": "Reproducer"}]
    assert json.loads((tmp_path / "task.json").read_text()) == task


@pytest.mark.parametrize("change", [
    {"state": "closed"}, {"pull_request": {}},
    {"labels": [{"name": "agentic-ignore"}]},
    {"labels": [{"name": "agentic-operations"}]},
])
def test_prepare_skips_ineligible_issues(worker, github, tmp_path, change):
    responses, calls = github
    responses["repos/owner/repo/issues/42"].update(change)
    assert worker.prepare("issue", "42", "owner/repo", "100", tmp_path)["skip"]
    assert not any("/comments" in path for path, _ in calls)


@pytest.mark.parametrize("existing", [
    pull(),
    pull(body="<!-- neurodesktop-agentic:issue:42 -->", head={"ref": "different", "repo": {"full_name": "owner/repo"}}),
])
def test_prepare_skips_existing_issue_proposal(worker, github, tmp_path, existing):
    responses, _ = github
    responses["repos/owner/repo/pulls?state=open&per_page=100"] = [existing]
    assert worker.prepare("issue", "42", "owner/repo", "100", tmp_path)["skip"]


@pytest.mark.parametrize("kind,subject,repo,run", [
    ("issue", "0", "owner/repo", "100"),
    ("issue", "1; echo bad", "owner/repo", "100"),
    ("issue", "42", "owner/repo/extra", "100"),
    ("issue", "42", "owner/repo", "invalid"),
    ("maintenance", "unknown", "owner/repo", "100"),
    ("unknown", "42", "owner/repo", "100"),
])
def test_prepare_rejects_invalid_input_before_network(worker, monkeypatch, tmp_path, kind, subject, repo, run):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid input reached GitHub")
    monkeypatch.setattr(worker, "api", unexpected)
    with pytest.raises(ValueError):
        worker.prepare(kind, subject, repo, run, tmp_path)


@pytest.mark.parametrize("category", ["updates", "security", "dead-code", "test-coverage", "refactoring"])
def test_maintenance_uses_stable_iso_week(worker, github, monkeypatch, tmp_path, category):
    class FrozenDate:
        @staticmethod
        def now(tz):
            return datetime(2027, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(worker, "datetime", FrozenDate)
    first = worker.prepare("maintenance", category, "owner/repo", "100", tmp_path)
    second = worker.prepare("maintenance", category, "owner/repo", "101", tmp_path)
    assert first["branch"] == second["branch"] == f"agentic/maintenance-{category}-2026-W53"
    assert not first["skip"]


def test_maintenance_does_not_recreate_closed_weekly_proposal(worker, github, tmp_path):
    responses, _ = github
    responses["closed"] = [pull(state="closed")]
    assert worker.prepare("maintenance", "security", "owner/repo", "100", tmp_path)["skip"]


def review_context(github, pr, count=0):
    responses, _ = github
    responses.update({
        "repos/owner/repo/pulls/42": pr,
        "repos/owner/repo/pulls/42/commits?per_page=100": [
            {"commit": {"message": "[agentic-review] fix"}} for _ in range(count)
        ],
        "repos/owner/repo/pulls/42/reviews?per_page=100": [{
            "user": {"login": "coderabbitai[bot]"},
            "commit_id": pr["head"]["sha"], "state": "COMMENTED",
        }],
        "repos/owner/repo/pulls/42/comments?per_page=100": [],
    })


@pytest.mark.parametrize("count,skip", [(0, False), (2, False), (3, True), (4, True)])
def test_review_has_three_commit_bound(worker, github, tmp_path, count, skip):
    review_context(github, pull(), count)
    task = worker.prepare("review", "42", "owner/repo", "100", tmp_path)
    assert task["skip"] is skip
    assert task["base_sha"] == "b" * 40
    assert task["validation_sha"] == "a" * 40


@pytest.mark.parametrize("pr", [
    pull(state="closed"), pull(user={"login": "stranger"}), pull(labels=[]),
    pull(head={"ref": "agentic/fix", "sha": "b" * 40, "repo": {"full_name": "attacker/fork"}}),
    pull(head={"ref": "ordinary-branch", "sha": "b" * 40, "repo": {"full_name": "owner/repo"}}),
])
def test_review_rejects_unowned_pr(worker, github, tmp_path, pr):
    review_context(github, pr)
    assert worker.prepare("review", "42", "owner/repo", "100", tmp_path)["skip"]


def test_docker_has_no_publisher_credentials_or_host_socket(worker, monkeypatch, tmp_path):
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.setenv(name, f"secret-{name}")
    args = worker.docker_args("task", tmp_path / "work", tmp_path / "control", tmp_path / "out", auth=tmp_path / "auth")
    joined = " ".join(args)
    for forbidden in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY", "docker.sock"):
        assert forbidden not in joined
    assert "--read-only" in args and "--cap-drop=ALL" in args
    assert f"type=bind,src={tmp_path / 'work' / '.git'},dst=/workspace/.git,readonly" in args


def test_validation_cannot_write_proposal_or_read_subscription(worker, tmp_path):
    args = worker.docker_args("check", tmp_path / "work", tmp_path / "control", tmp_path / "out")
    assert "--network=none" in args
    assert not any("/codex" in arg or "CODEX_HOME" in arg for arg in args)
    outputs = [arg for arg in args if "dst=/output" in arg]
    assert not outputs or all(arg.endswith(",readonly") for arg in outputs)


def valid_result(**changes):
    result = {"outcome": "change", "title": "Repair behavior", "body": "A tested fix.", "pending_validation": []}
    result.update(changes)
    return result


@pytest.mark.parametrize("result", [
    None, [], {}, valid_result(extra="unexpected"), valid_result(outcome="merge"),
    valid_result(title=" "), valid_result(title="x" * 161),
    valid_result(body="x" * 30001), valid_result(pending_validation="pytest"),
    valid_result(pending_validation=[1]), valid_result(pending_validation=["x"] * 51),
])
def test_result_schema_rejects_bad_values(worker, result):
    with pytest.raises(ValueError):
        worker.validate_result(result)


def test_result_preserves_shell_shaped_text_as_data(worker):
    result = valid_result(title="$(touch /tmp/not-executed)", body="`echo arbitrary` remains data")
    assert worker.validate_result(result) == result


@pytest.fixture
def git_repo(worker, tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    worker.git("init", "-b", "main", cwd=repo)
    worker.git("config", "user.name", "Test", cwd=repo)
    worker.git("config", "user.email", "test@example.invalid", cwd=repo)
    (repo / "file.txt").write_text("before\n")
    worker.git("add", "file.txt", cwd=repo)
    worker.git("commit", "-m", "base", cwd=repo)
    return repo


def test_check_patch_reads_real_staged_content(worker, git_repo):
    (git_repo / "file.txt").write_text("after\n")
    worker.git("add", "file.txt", cwd=git_repo)
    patch = worker.check_patch(git_repo)
    assert b"-before\n+after" in patch


def test_check_patch_round_trips_non_utf8_text_and_binary_blobs(worker, git_repo, tmp_path):
    text_path = git_repo / "non-utf8.txt"
    blob_path = git_repo / "blob.bin"
    text_path.write_bytes(b"before\n")
    blob_path.write_bytes(b"before\x00blob\n")
    worker.git("add", "non-utf8.txt", "blob.bin", cwd=git_repo)
    worker.git("commit", "-m", "binary fixtures", cwd=git_repo)

    expected_text = b"after\xfftext\n"
    expected_blob = b"after\x00blob\xff\n"
    text_path.write_bytes(expected_text)
    blob_path.write_bytes(expected_blob)
    worker.git("add", "non-utf8.txt", "blob.bin", cwd=git_repo)

    patch = worker.check_patch(git_repo)
    patch_path = tmp_path / "change.patch"
    patch_path.write_bytes(patch)
    worker.git("reset", "--hard", "HEAD", cwd=git_repo)
    worker.git("apply", "--index", str(patch_path), cwd=git_repo)

    assert text_path.read_bytes() == expected_text
    assert blob_path.read_bytes() == expected_blob


@pytest.mark.parametrize("name", [".env", "config/auth.json", "nested/credentials.json"])
def test_check_patch_rejects_credentials(worker, git_repo, name):
    path = git_repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("credential")
    worker.git("add", "--all", cwd=git_repo)
    with pytest.raises(ValueError, match="credential"):
        worker.check_patch(git_repo)


def test_check_patch_rejects_new_symlink(worker, git_repo):
    (git_repo / "leak").symlink_to("/codex/auth.json")
    worker.git("add", "--all", cwd=git_repo)
    with pytest.raises(ValueError, match="symlinks"):
        worker.check_patch(git_repo)


def test_check_patch_bounds_number_of_files(worker, git_repo):
    for i in range(51):
        (git_repo / f"extra-{i}").write_text("extra\n")
    worker.git("add", "--all", cwd=git_repo)
    with pytest.raises(ValueError, match="50"):
        worker.check_patch(git_repo)


@pytest.fixture
def proposal(worker, git_repo, tmp_path):
    remote = tmp_path / "remote.git"
    worker.git("init", "--bare", str(remote))
    worker.git("remote", "add", "origin", str(remote), cwd=git_repo)
    worker.git("push", "origin", "main", cwd=git_repo)
    base = worker.git("rev-parse", "HEAD", cwd=git_repo)
    (git_repo / "file.txt").write_text("after\n")
    worker.git("add", "--all", cwd=git_repo)
    output = tmp_path / "output"
    output.mkdir()
    (output / "change.patch").write_bytes(worker.check_patch(git_repo))
    worker.git("reset", "--hard", base, cwd=git_repo)
    worker.write_json(output / "result.json", valid_result(pending_validation=["Container image tests"]))
    proof = {"passed": True, "patch_sha256": hashlib.sha256((output / "change.patch").read_bytes()).hexdigest()}
    (output / "validation.txt").write_text(json.dumps(proof) + "\n12 passed in 1.0s\n")
    task = {"kind": "issue", "subject": "42", "repo": "owner/repo", "run_id": "100", "branch": "agentic/issue-42-100", "base_sha": base, "base_branch": "main"}
    return task, output, git_repo, remote


@pytest.fixture
def publisher_api(worker, monkeypatch):
    calls, created = [], []
    comments = {}
    def pages(path):
        if "/pulls?" in path:
            return created
        if "/comments?" in path:
            return comments.get(path.split("?")[0], [])
        raise AssertionError(path)
    def api(path, payload=None):
        calls.append((path, payload))
        if path == "repos/owner/repo/pulls":
            pr = {"number": 91, "state": "open", "title": payload["title"], "html_url": "https://github.com/owner/repo/pull/91", "user": {"login": "github-actions[bot]"}, "head": {"ref": payload["head"], "repo": {"full_name": "owner/repo"}}, "body": payload["body"]}
            created.append(pr)
            return pr
        if path == "repos/owner/repo/pulls/91":
            return created[0]
        if path.endswith("/comments"):
            comments.setdefault(path, []).append({"user": {"login": "github-actions[bot]"}, "body": payload["body"]})
        return {}
    monkeypatch.setattr(worker, "pages", pages)
    monkeypatch.setattr(worker, "api", api)
    return calls, created, api


def test_publish_pushes_real_patch_and_creates_one_draft(worker, proposal, publisher_api):
    task, output, repo, remote = proposal
    calls, created, _ = publisher_api
    assert worker.publish(task, output, repo) == 91
    assert worker.git("show", f"{task['branch']}:file.txt", cwd=remote) == "after"
    request = next(payload for path, payload in calls if path == "repos/owner/repo/pulls")
    assert request["draft"] is True
    assert "12 passed" in request["body"]
    assert "Container image tests" in request["body"]
    assert "Fixes #42" in request["body"]
    assert worker.publish(task, output, repo) == 91
    assert len(created) == 1


@pytest.mark.parametrize("log", ["FAILED: tests\n", "No tests ran\n", "1 passed, 1 failed\n"])
def test_publish_refuses_failed_or_absent_independent_validation(worker, proposal, publisher_api, log):
    task, output, repo, _ = proposal
    (output / "validation.txt").write_text(log)
    with pytest.raises(ValueError, match="validation"):
        worker.publish(task, output, repo)
    assert not publisher_api[1]


def test_publish_retries_after_push_before_pr_creation(worker, proposal, publisher_api, monkeypatch):
    task, output, repo, _ = proposal
    _, _, real_api = publisher_api
    def fail_create(path, payload=None):
        if path == "repos/owner/repo/pulls":
            raise RuntimeError("GitHub temporarily unavailable")
        return real_api(path, payload)
    monkeypatch.setattr(worker, "api", fail_create)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00+00:00")
    with pytest.raises(RuntimeError, match="temporarily"):
        worker.publish(task, output, repo)
    monkeypatch.setattr(worker, "api", real_api)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:01:00+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:01:00+00:00")
    assert worker.publish(task, output, repo) == 91


def test_publish_refuses_stale_review(worker, proposal, publisher_api, monkeypatch):
    task, output, repo, _ = proposal
    task["kind"] = "review"
    worker.git("checkout", "-b", task["branch"], cwd=repo)
    (repo / "file.txt").write_text("Human changes\n")
    worker.git("add", "--all", cwd=repo)
    worker.git("commit", "-m", "Human revision", cwd=repo)
    worker.git("push", "origin", task["branch"], cwd=repo)
    worker.git("checkout", "main", cwd=repo)
    monkeypatch.setattr(worker, "api", lambda *args, **kwargs: {"state": "open", "head": {"sha": "changed"}})
    with pytest.raises(ValueError, match="stale"):
        worker.publish(task, output, repo)


@pytest.mark.parametrize("auth_data", [
    {"auth_mode": "apikey", "OPENAI_API_KEY": "key"},
    {"auth_mode": "chatgpt", "tokens": {}},
])
def test_worker_requires_subscription_authentication(worker, tmp_path, auth_data):
    worker.write_json(tmp_path / "auth.json", auth_data)
    with pytest.raises(ValueError, match="subscription"):
        worker.token_values(tmp_path)


@pytest.mark.parametrize("conclusion", ["success", "failure", "timed_out"])
def test_failure_evidence_reads_jobs_from_paginated_objects(worker, monkeypatch, conclusion):
    jobs = [{"id": 123, "name": "check", "conclusion": conclusion}]
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ["gh", "api"]:
            if "--paginate" in args:
                return json.dumps([
                    {"total_count": 2, "jobs": [{"id": 122, "conclusion": "success"}]},
                    {"total_count": 2, "jobs": jobs},
                ])
            return json.dumps({"id": 20})
        assert args[:3] == ["gh", "run", "view"]
        return "failed job evidence"

    monkeypatch.setattr(worker, "command", command)
    evidence = worker.failure_context("owner/repo", "https://github.com/owner/repo/actions/runs/20")
    assert evidence[0]["jobs"] == ([] if conclusion == "success" else jobs)
    if conclusion == "success":
        assert evidence[0]["logs"] == []
    else:
        record = evidence[0]["logs"][0]
        assert record["job_id"] == 123
        assert record["unattributed"]["tail"] == "failed job evidence"
        assert record["steps"] == []
    assert len(calls) == (2 if conclusion == "success" else 3)


def test_pages_preserves_list_responses(worker, monkeypatch):
    monkeypatch.setattr(worker, "command", lambda args: json.dumps([[{"id": 1}], [], [{"id": 2}]]))
    assert worker.pages("repos/owner/repo/pulls") == [{"id": 1}, {"id": 2}]


def test_failure_evidence_uses_only_this_repository_and_bounded_logs(worker, monkeypatch):
    calls = []
    def api(path):
        calls.append(path)
        return {"id": path.rsplit("/", 1)[-1]}
    def pages(path, *, key):
        assert key == "jobs"
        calls.append(path)
        return [{"id": i, "name": f"job-{i}", "conclusion": "failure"} for i in range(4)]
    def command(args):
        calls.append(args)
        return "x" * 21000
    monkeypatch.setattr(worker, "api", api)
    monkeypatch.setattr(worker, "pages", pages)
    monkeypatch.setattr(worker, "command", command)
    body = " ".join([
        "https://github.com/attacker/fork/actions/runs/10",
        "https://github.com/owner/repo/actions/runs/20",
        "https://github.com/owner/repo/actions/runs/20",
        "https://github.com/owner/repo/actions/runs/30",
        "https://github.com/owner/repo/actions/runs/40",
    ])
    evidence = worker.failure_context("owner/repo", body)
    assert [item["run"]["id"] for item in evidence] == ["20", "40"]
    assert all(len(item["logs"]) == 4 for item in evidence)
    assert len(json.dumps(evidence)) <= 40000
    assert evidence[0]["omitted_runs"] == 1
    assert all("Earlier evidence truncated" in log["unattributed"]["tail"] for item in evidence for log in item["logs"])
    assert not any("attacker" in str(call) for call in calls)


def test_failure_evidence_discards_verbose_api_metadata(worker, monkeypatch):
    monkeypatch.setattr(worker, "api", lambda _path: {
        "id": 20,
        "name": "Build neurodesktop-test",
        "head_sha": "a" * 40,
        "repository": {"description": "x" * 30000},
        "actor": {"login": "builder"},
    })
    monkeypatch.setattr(worker, "pages", lambda _path, *, key: [{
        "id": 123,
        "name": "scan-image (amd64)",
        "conclusion": "failure",
        "steps": [{"name": "Container image scan", "conclusion": "failure"}],
        "runner_group_name": "GitHub Actions",
        "labels": ["ubuntu-22.04"],
    }])
    monkeypatch.setattr(worker, "command", lambda _args: "critical finding")

    evidence = worker.failure_context(
        "owner/repo", "https://github.com/owner/repo/actions/runs/20",
    )

    assert evidence[0]["run"] == {
        "id": 20,
        "name": "Build neurodesktop-test",
        "head_sha": "a" * 40,
    }
    assert "runner_group_name" not in evidence[0]["jobs"][0]
    assert "labels" not in evidence[0]["jobs"][0]


def test_failure_evidence_budget_keeps_the_end_of_both_failed_job_logs(worker, monkeypatch):
    monkeypatch.setattr(worker, "api", lambda path: {"id": 20, "name": "Build neurodesktop-test"})
    monkeypatch.setattr(worker, "pages", lambda path, key: [
        {"id": arch, "name": f"scan-image ({arch})", "conclusion": "failure"} for arch in ("amd64", "arm64")
    ])
    monkeypatch.setattr(worker, "command", lambda args: "a" * 30000 + "\n" + args[args.index("--job") + 1].upper() + "-CVE-END")
    evidence = worker.failure_context("owner/repo", "https://github.com/owner/repo/actions/runs/20")
    bounded = worker.bounded_context({"failure_evidence": evidence})
    serialized = json.dumps(bounded)

    assert "AMD64-CVE-END" in serialized
    assert "ARM64-CVE-END" in serialized
    assert "scan-image (amd64)" in serialized
    assert "scan-image (arm64)" in serialized
    assert "Earlier evidence truncated" in serialized
    assert len(serialized) <= 40000


def test_snapshot_validation_baseline_reads_the_requested_base_revision(worker, git_repo, tmp_path):
    tests = git_repo / "tests"
    (tests / "unit").mkdir(parents=True)
    (tests / "testlib.py").write_text("base helper\n")
    (tests / "pytest.ini").write_text("[pytest]\n")
    test = tests / "unit" / "test_behavior.py"
    test.write_text("BASE = True\n")
    worker.git("add", "tests", cwd=git_repo)
    worker.git("commit", "-m", "base tests", cwd=git_repo)
    base_sha = worker.git("rev-parse", "HEAD", cwd=git_repo)

    test.write_text("CANDIDATE = True\n")
    baseline = tmp_path / "baseline"
    worker.snapshot_validation_baseline(git_repo, base_sha, baseline)

    assert (baseline / "tests" / "unit" / "test_behavior.py").read_text() == "BASE = True\n"
    assert (baseline / "tests" / "pytest.ini").read_text() == "[pytest]\n"


@pytest.fixture
def candidate(worker, git_repo, tmp_path, monkeypatch):
    control, output, auth = (tmp_path / name for name in ("control", "candidate-output", "auth"))
    for path in (control, output, auth):
        path.mkdir()
    (control / "prompt.md").write_text("Investigate the issue and fix it.")
    token = "subscription-token-" + "x" * 40
    worker.write_json(auth / "auth.json", {"auth_mode": "chatgpt", "tokens": {"access_token": token}})
    tests = git_repo / "tests"
    (tests / "unit").mkdir(parents=True)
    (tests / "testlib.py").write_text(
        "from pathlib import Path\n"
        "TESTS_DIR = Path(__file__).resolve().parent\n"
        "REPO_ROOT = TESTS_DIR.parent\n"
        "def repo_path(relative):\n"
        "    return REPO_ROOT / relative\n"
    )
    (tests / "conftest.py").write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n")
    (tests / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    (tests / "unit" / "test_file.py").write_text(
        "from testlib import repo_path\n\n"
        "def test_candidate_patch():\n"
        "    assert repo_path('file.txt').read_text() == 'after\\n'\n"
    )
    worker.git("add", "tests", cwd=git_repo)
    worker.git("commit", "-m", "base test suite", cwd=git_repo)
    state = {
        "calls": [], "result": valid_result(), "edit": "after\n", "validation_error": None,
        "base_sha": worker.git("rev-parse", "HEAD", cwd=git_repo),
    }
    original_command, original_run = worker.command, subprocess.run
    def command(args, **kwargs):
        if args[0] != "docker":
            return original_command(args, **kwargs)
        state["calls"].append(args)
        if "codex" in args:
            if state["edit"] is not None:
                (git_repo / "file.txt").write_text(state["edit"])
            worker.write_json(output / "result.json", state["result"])
            return ""
        if state["validation_error"]:
            raise state["validation_error"]
        return "3 passed in 0.1s\n"
    def run(args, **kwargs):
        if args[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(args, 0)
        return original_run(args, **kwargs)
    monkeypatch.setattr(worker, "command", command)
    monkeypatch.setattr(worker.subprocess, "run", run)
    return state, git_repo, control, output, auth, token


def test_candidate_runs_independent_credential_free_validation(worker, candidate):
    state, repo, control, output, auth, _ = candidate
    worker.run_candidate({"kind": "issue", "base_sha": state["base_sha"], "validation_sha": state["base_sha"]}, repo, control, output, auth, "chosen-model")
    assert len(state["calls"]) == 2
    agent, validation = state["calls"]
    assert "chosen-model" in agent and 'forced_login_method="chatgpt"' in agent
    codex_index = agent.index("codex")
    assert agent[codex_index - 5:codex_index + 2] == [
        "5350s", "sh", "-c",
        'python /control/validate.py --prepare-workspace /workspace && exec "$@"',
        "sh", "codex", "exec",
    ]
    assert "--network=none" in validation
    assert str(auth) not in " ".join(validation)
    assert any("dst=/baseline,readonly" in arg for arg in validation)
    assert validation[-6:] == ["python", "/control/validate.py", "--baseline", "/baseline", "--workspace", "/workspace"]
    assert "+after" in (output / "change.patch").read_text()
    proof, log = (output / "validation.txt").read_text().split("\n", 1)
    assert json.loads(proof) == {"passed": True, "patch_sha256": hashlib.sha256((output / "change.patch").read_bytes()).hexdigest()}
    assert log == "3 passed in 0.1s\n"


@pytest.mark.parametrize("error", [RuntimeError("failed"), subprocess.TimeoutExpired("pytest", 1800)])
def test_candidate_failed_validation_becomes_blocked(worker, candidate, error):
    state, repo, control, output, auth, _ = candidate
    state["validation_error"] = error
    worker.run_candidate({"kind": "issue", "base_sha": state["base_sha"], "validation_sha": state["base_sha"]}, repo, control, output, auth, "chosen-model")
    result = json.loads((output / "result.json").read_text())
    assert result["outcome"] == "blocked"
    proof, log = (output / "validation.txt").read_text().split("\n", 1)
    assert json.loads(proof)["passed"] is False
    assert log.startswith("FAILED:")


@pytest.mark.parametrize("location", ["patch", "result"])
def test_candidate_rejects_subscription_token_in_artifacts(worker, candidate, location):
    state, repo, control, output, auth, token = candidate
    if location == "patch":
        state["edit"] = token + "\n"
    else:
        state["result"]["body"] = token
    with pytest.raises(ValueError, match="Credential"):
        worker.run_candidate({"kind": "issue", "base_sha": state["base_sha"], "validation_sha": state["base_sha"]}, repo, control, output, auth, "chosen-model")
    assert not (output / "change.patch").exists()
    assert not (output / "result.json").exists()
    assert len(state["calls"]) == 1


def test_candidate_refuses_pr_without_patch(worker, candidate):
    state, repo, control, output, auth, _ = candidate
    state["edit"] = None
    with pytest.raises(ValueError, match="without a patch"):
        worker.run_candidate({"kind": "issue", "base_sha": state["base_sha"], "validation_sha": state["base_sha"]}, repo, control, output, auth, "chosen-model")
    assert len(state["calls"]) == 1


def test_candidate_rejects_auth_inside_checkout(worker, candidate):
    state, repo, control, output, _, _ = candidate
    auth = repo / "auth"
    auth.mkdir()
    with pytest.raises(ValueError, match="outside the checkout"):
        worker.run_candidate({"kind": "issue", "base_sha": state["base_sha"], "validation_sha": state["base_sha"]}, repo, control, output, auth, "chosen-model")
    assert not state["calls"]


@pytest.mark.parametrize("existing", [
    pull(body="<!-- neurodesktop-agentic:issue:42 -->", user={"login": "attacker"}),
    pull(body="<!-- neurodesktop-agentic:issue:42 -->", head={"ref": "agentic/issue-42-100", "repo": {"full_name": "attacker/fork"}}),
    pull(body="<!-- neurodesktop-agentic:issue:42 -->", head={"ref": "agentic/issue-42-100", "repo": None}),
])
def test_issue_marker_spoof_cannot_suppress_worker(worker, github, tmp_path, existing):
    responses, _ = github
    responses["repos/owner/repo/pulls?state=open&per_page=100"] = [existing]
    assert not worker.prepare("issue", "42", "owner/repo", "100", tmp_path)["skip"]


def test_publisher_ignores_foreign_proposal_marker(worker, proposal, publisher_api):
    task, output, repo, _ = proposal
    calls, created, _ = publisher_api
    created.append(pull(body=worker.marker(task), user={"login": "attacker"}))
    assert worker.publish(task, output, repo) == 91
    assert len([path for path, _ in calls if path == "repos/owner/repo/pulls"]) == 1


def test_publisher_rejects_patch_changed_after_validation(worker, proposal, publisher_api):
    task, output, repo, _ = proposal
    path = output / "change.patch"
    path.write_text(path.read_text().replace("+after", "+unvalidated"))
    with pytest.raises(ValueError, match="does not match"):
        worker.publish(task, output, repo)
    assert not publisher_api[0]


def test_publisher_rejects_failed_validation_proof(worker, proposal, publisher_api):
    task, output, repo, _ = proposal
    path = output / "validation.txt"
    proof, log = path.read_text().split("\n", 1)
    proof = json.loads(proof)
    proof["passed"] = False
    path.write_text(json.dumps(proof) + "\n" + log)
    with pytest.raises(ValueError, match="does not match"):
        worker.publish(task, output, repo)
    assert not publisher_api[0]


def test_review_retries_after_push_without_another_commit(worker, proposal, publisher_api, monkeypatch):
    task, output, repo, remote = proposal
    task["kind"] = "review"
    worker.git("push", "origin", f"HEAD:refs/heads/{task['branch']}", cwd=repo)
    state = {"fail_comment": True}
    _, _, original_api = publisher_api
    def api(path, payload=None):
        if path == "repos/owner/repo/pulls/42":
            return pull(head={**pull()["head"], "sha": worker.git("rev-parse", task["branch"], cwd=remote)})
        if path == "repos/owner/repo/issues/42/comments" and state["fail_comment"]:
            raise RuntimeError("Comment service unavailable")
        return original_api(path, payload)
    monkeypatch.setattr(worker, "api", api)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00+00:00")
    with pytest.raises(RuntimeError, match="Comment service"):
        worker.publish(task, output, repo)
    pushed = worker.git("rev-parse", task["branch"], cwd=remote)
    state["fail_comment"] = False
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:01:00+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:01:00+00:00")
    assert worker.publish(task, output, repo) == 42
    assert worker.git("rev-parse", task["branch"], cwd=remote) == pushed
    assert worker.git("log", "--format=%s", task["branch"], cwd=remote).count("[agentic-review]") == 1


@pytest.fixture
def credential_push(worker, monkeypatch):
    original = worker.command
    state = {"pushes": [], "fail_once": False}
    def command(args, **kwargs):
        env = kwargs.get("env") or {}
        if "push" in args and env.get("GIT_CONFIG_VALUE_1", "").startswith("AUTHORIZATION:"):
            assert "private-test-token" not in " ".join(args)
            assert "AUTHORIZATION" not in " ".join(args)
            state["pushes"].append({"args": args, "env": dict(env)})
            if state["fail_once"]:
                state["fail_once"] = False
                raise RuntimeError("Token push temporarily unavailable")
            kwargs["env"] = worker.GIT_ENV
        return original(args, **kwargs)
    monkeypatch.setattr(worker, "command", command)
    return state


def test_existing_pr_repairs_failed_labels_without_another_commit(worker, proposal, publisher_api, monkeypatch):
    task, output, repo, remote = proposal
    calls, created, original = publisher_api
    state = {"fail": True}
    def api(path, payload=None):
        if path.endswith("/labels") and state["fail"]:
            state["fail"] = False
            raise RuntimeError("Labels unavailable")
        return original(path, payload)
    monkeypatch.setattr(worker, "api", api)
    with pytest.raises(RuntimeError, match="Labels"):
        worker.publish(task, output, repo)
    first_head = worker.git("rev-parse", task["branch"], cwd=remote)
    assert worker.publish(task, output, repo) == 91
    assert worker.publish(task, output, repo) == 91
    assert len(created) == 1
    assert worker.git("rev-parse", task["branch"], cwd=remote) == first_head
    requests = [body for path, body in calls if path.endswith("/comments") and "@coderabbitai" in body["body"]]
    assert len(requests) == 1
    assert requests[0]["body"].startswith("<!-- neurodesktop-agentic-review-request:100 -->\n")


@pytest.mark.parametrize("kind", ["issue", "review"])
def test_ci_push_replay_does_not_duplicate_empty_commit(worker, proposal, publisher_api, credential_push, monkeypatch, kind):
    task, output, repo, remote = proposal
    task["kind"] = kind
    _, _, original = publisher_api
    if kind == "review":
        worker.git("push", "origin", f"HEAD:refs/heads/{task['branch']}", cwd=repo)
    state = {"fail": True}
    def api(path, payload=None):
        if path == "repos/owner/repo/pulls/42":
            return pull(head={**pull()["head"], "sha": worker.git("rev-parse", task["branch"], cwd=remote)})
        if path.endswith("/comments") and state["fail"]:
            state["fail"] = False
            raise RuntimeError("Comment unavailable after CI push")
        return original(path, payload)
    monkeypatch.setattr(worker, "api", api)
    monkeypatch.setenv("AGENTIC_CI_TOKEN", "private-test-token")
    with pytest.raises(RuntimeError, match="after CI push"):
        worker.publish(task, output, repo)
    first_head = worker.git("rev-parse", task["branch"], cwd=remote)
    assert worker.publish(task, output, repo) == (42 if kind == "review" else 91)
    assert worker.git("rev-parse", task["branch"], cwd=remote) == first_head
    subjects = worker.git("log", task["branch"], "--format=%s", cwd=remote).splitlines()
    assert subjects.count("[agentic-ci] Run pull request checks (100)") == 1
    assert len(credential_push["pushes"]) == 1
    assert "AUTHORIZATION" not in (repo / ".git" / "config").read_text()


def test_comment_dedup_requires_bot_and_exact_first_line(worker, monkeypatch):
    task = {"repo": "owner/repo", "subject": "42", "run_id": "100"}
    key = "<!-- neurodesktop-agentic-review-request:100 -->"
    comments = [
        {"user": {"login": "attacker"}, "body": key + "\nforged"},
        {"user": {"login": "github-actions[bot]"}, "body": "Quoted marker\n" + key},
    ]
    calls = []
    monkeypatch.setattr(worker, "pages", lambda path: comments)
    def api(path, payload):
        calls.append((path, payload))
        comments.append({"user": {"login": "github-actions[bot]"}, "body": payload["body"]})
    monkeypatch.setattr(worker, "api", api)
    worker.comment_once(task, "@coderabbitai review", number=91, purpose="review-request")
    worker.comment_once(task, "@coderabbitai review", number=91, purpose="review-request")
    assert len(calls) == 1
    assert calls[0][0] == "repos/owner/repo/issues/91/comments"


def test_workflow_patch_seeds_base_and_retries_scoped_push(worker, proposal, publisher_api, credential_push, monkeypatch):
    task, output, repo, remote = proposal
    path = repo / ".github" / "workflows" / "example.yml"
    path.parent.mkdir(parents=True)
    path.write_text("name: Example\non: workflow_dispatch\njobs: {}\n")
    worker.git("add", "--all", cwd=repo)
    patch = worker.check_patch(repo)
    (output / "change.patch").write_bytes(patch)
    proof = {"passed": True, "patch_sha256": hashlib.sha256(patch).hexdigest()}
    (output / "validation.txt").write_text(json.dumps(proof) + "\n12 passed\n")
    worker.git("reset", "--hard", task["base_sha"], cwd=repo)
    monkeypatch.setenv("AGENTIC_PUSH_TOKEN", "private-test-token")
    monkeypatch.setenv("AGENTIC_CI_TOKEN", "private-test-token")
    credential_push["fail_once"] = True
    with pytest.raises(RuntimeError, match="Token push"):
        worker.publish(task, output, repo)
    assert worker.git("rev-parse", task["branch"], cwd=remote) == task["base_sha"]
    assert worker.publish(task, output, repo) == 91
    message = worker.git("log", "-1", "--format=%B", task["branch"], cwd=remote)
    assert "[skip ci]" in message
    assert "[agentic-ci]" not in worker.git("log", task["branch"], "--format=%s", cwd=remote)
    assert len(credential_push["pushes"]) == 2
    calls, created, _ = publisher_api
    assert len(created) == 1
    notices = [body for path, body in calls if path.endswith("/comments") and "ci-review-required" in body["body"]]
    assert len(notices) == 1
    assert "AUTHORIZATION" not in (repo / ".git" / "config").read_text()


def test_issue_dedup_ignores_marker_quoted_in_another_owned_pr(worker, github, tmp_path):
    responses, _ = github
    responses['repos/owner/repo/pulls?state=open&per_page=100'] = [pull(
        body='<!-- neurodesktop-agentic:issue:99 -->\nQuoted evidence:\n<!-- neurodesktop-agentic:issue:42 -->',
        head={'ref': 'agentic/issue-99-100', 'repo': {'full_name': 'owner/repo'}},
    )]
    assert not worker.prepare('issue', '42', 'owner/repo', '101', tmp_path)['skip']


def test_publisher_does_not_reuse_pr_with_quoted_marker(worker, proposal, publisher_api):
    task, output, repo, _ = proposal
    _, created, _ = publisher_api
    unrelated = pull(
        number=92,
        body='<!-- neurodesktop-agentic:issue:99 -->\nQuoted evidence:\n' + worker.marker(task),
        head={'ref': 'agentic/issue-99-100', 'repo': {'full_name': 'owner/repo'}},
    )
    created.append(unrelated)
    assert worker.publish(task, output, repo) == 91
    assert len(created) == 2


def test_large_unrelated_pr_bodies_do_not_fill_issue_context(worker, github, tmp_path):
    responses, _ = github
    responses['repos/owner/repo/pulls?state=open&per_page=100'] = [
        pull(number=number, user={'login': 'contributor'}, body='x' * 65000)
        for number in range(100, 104)
    ]
    task = worker.prepare('issue', '42', 'owner/repo', '101', tmp_path)
    assert not task['skip']
    assert task['context']['issue']['body'] == 'Broken'
    assert len(json.dumps(task)) < 10000
    assert all('body' not in pr for pr in task['context']['open_pull_requests'])


def test_large_review_context_preserves_subject_and_recent_feedback(worker, github, tmp_path):
    responses, _ = github
    pr = pull(body='Primary problem: ' + '\U0001f600' * 60000)
    pr['base'] = {'sha': 'c' * 40}
    review_context(github, pr)
    feedback = [{'body': f'Feedback {number}: ' + 'x' * 65000} for number in range(100)]
    for path in ('reviews', 'comments'):
        responses[f'repos/owner/repo/pulls/42/{path}?per_page=100'] = feedback
    responses['repos/owner/repo/issues/42/comments?per_page=100'] = feedback
    task = worker.prepare('review', '42', 'owner/repo', '101', tmp_path)
    assert len(json.dumps(task)) < 200000
    context = task['context']
    assert context['pull_request']['body'].startswith('Primary problem:')
    assert context['pull_request']['base']['sha'] == 'c' * 40
    assert context['comments'][-1]['body'].startswith('Feedback 99:')
    assert 'truncation_notice' in context


def test_review_comment_contains_current_revision_and_validation(worker, proposal, publisher_api, monkeypatch):
    task, output, repo, remote = proposal
    task['kind'] = 'review'
    worker.git('push', 'origin', f"HEAD:refs/heads/{task['branch']}", cwd=repo)
    calls, _, original_api = publisher_api
    def api(path, payload=None):
        if path == 'repos/owner/repo/pulls/42':
            return pull(head={**pull()['head'], 'sha': worker.git('rev-parse', task['branch'], cwd=remote)})
        return original_api(path, payload)
    monkeypatch.setattr(worker, 'api', api)
    worker.write_json(output / 'result.json', valid_result(pending_validation=['New subsystem image check']))
    assert worker.publish(task, output, repo) == 42
    revision = worker.git('rev-parse', task['branch'], cwd=remote)
    comment = next(payload['body'] for path, payload in calls
                   if path.endswith('/comments') and 'Review revision:' in payload['body'])
    assert comment.startswith(worker.review_marker(task) + '\n')
    assert revision in comment
    assert '12 passed in 1.0s' in comment
    assert 'New subsystem image check' in comment


def test_result_reserves_room_for_publication_evidence(worker):
    with pytest.raises(ValueError, match="insufficient room"):
        worker.validate_result(valid_result(body="b" * 30000,
                                            pending_validation=["p" * 1000] * 50))


def test_fetch_revision_materializes_an_older_task_without_checking_it_out(worker, proposal, tmp_path):
    task, _, repo, remote = proposal
    (repo / "file.txt").write_text("new default branch\n")
    worker.git("add", "--all", cwd=repo)
    worker.git("commit", "-m", "Later main", cwd=repo)
    worker.git("push", "origin", "main", cwd=repo)
    checkout = tmp_path / "fresh"
    worker.git("clone", "--depth=1", "--branch", "main", remote.as_uri(), str(checkout))
    current = worker.git("rev-parse", "HEAD", cwd=checkout)
    with pytest.raises(worker.CommandFailure):
        worker.git("cat-file", "-e", task["base_sha"], cwd=checkout)
    worker.fetch_revision(checkout, task["base_sha"])
    assert worker.git("cat-file", "-t", task["base_sha"], cwd=checkout) == "commit"
    assert worker.git("rev-parse", "HEAD", cwd=checkout) == current


def test_fetch_revision_rejects_refs_and_options_before_git(worker, monkeypatch, tmp_path):
    def forbidden(*a, **k):
        pytest.fail("Invalid revision reached git")
    monkeypatch.setattr(worker, "git", forbidden)
    with pytest.raises(ValueError, match="revision SHA"):
        worker.fetch_revision(tmp_path, "--upload-pack=evil")


@pytest.mark.parametrize("change", [
    {"commit_id": "c" * 40}, {"state": "PENDING"},
    {"user": {"login": "stranger"}},
])
def test_review_requires_completed_coderabbit_review_of_current_head(worker, github, tmp_path, change):
    review_context(github, pull())
    github[0]["repos/owner/repo/pulls/42/reviews?per_page=100"][0].update(change)
    assert worker.prepare("review", "42", "owner/repo", "100", tmp_path)["skip"]


@pytest.mark.parametrize("author,head,skip", [
    ("github-actions[bot]", "b", True), ("stranger", "b", False),
    ("github-actions[bot]", "c", False),
])
def test_review_deduplicates_completed_head_by_trusted_marker(worker, github, tmp_path, author, head, skip):
    review_context(github, pull())
    github[0]["repos/owner/repo/issues/42/comments?per_page=100"] = [{
        "user": {"login": author},
        "body": worker.review_marker({"base_sha": head * 40}) + "\nCompleted",
    }]
    assert worker.prepare("review", "42", "owner/repo", "101", tmp_path)["skip"] is skip


@pytest.mark.parametrize("outcome", ["no_change", "blocked"])
def test_review_without_patch_records_head_once(worker, proposal, publisher_api, outcome):
    task, output, repo, _ = proposal
    task["kind"] = "review"
    worker.write_json(output / "result.json", valid_result(outcome=outcome))
    worker.publish(task, output, repo)
    worker.publish({**task, "run_id": "101"}, output, repo)
    bodies = [payload["body"] for path, payload in publisher_api[0] if path.endswith("/comments")]
    assert len(bodies) == 1
    assert bodies[0].startswith(worker.review_marker(task) + "\n")


@pytest.mark.parametrize("operation", ["fetch", "ls-remote", "push"])
def test_git_uses_ephemeral_publisher_auth(worker, monkeypatch, tmp_path, operation):
    import base64
    monkeypatch.setenv("GH_TOKEN", "publisher-token")
    monkeypatch.setenv("AGENTIC_PUSH_TOKEN", "workflow-token")
    calls = []
    def command(args, **kwargs):
        calls.append((args, kwargs))
        return ""
    monkeypatch.setattr(worker, "command", command)
    worker.git(operation, "origin", "main", cwd=tmp_path)
    args, kwargs = calls[0]
    assert "publisher-token" not in str(args)
    assert kwargs["env"]["GIT_CONFIG_VALUE_0"] == ""
    header = kwargs["env"]["GIT_CONFIG_VALUE_1"].split()[-1]
    assert base64.b64decode(header) == b"x-access-token:publisher-token"
    assert not list(tmp_path.iterdir())


def test_stage_cli_copies_trusted_controller_and_locked_lint_context(tmp_path):
    destination = tmp_path / "control"
    destination.mkdir()

    subprocess.run([
        sys.executable, str(repo_path(".github/scripts/agentic_worker.py")), "stage",
        "--workspace", str(repo_path(".")), "--directory", str(destination),
    ], check=True, capture_output=True, text=True)

    for script in ("agentic_worker.py", "check_agentic_sandbox.py"):
        assert (destination / ".github" / "scripts" / script).read_bytes() == repo_path(f".github/scripts/{script}").read_bytes()
    assert not (destination / ".github" / "scripts" / "check_agentic_readiness.py").exists()
    for manifest in ("package.json", "package-lock.json"):
        assert (destination / "config" / "agentic" / "javascript-lint" / manifest).read_bytes() == repo_path(f"tools/javascript-lint/{manifest}").read_bytes()
    assert not (destination / "config" / "agentic" / "javascript-lint" / "node_modules").exists()
    assert not (destination / ".git").exists()
    assert not (destination / "tests").exists()


def test_checkout_canary_uses_clean_disposable_head_and_offline_validator(worker, git_repo, monkeypatch, tmp_path):
    tests = git_repo / "tests"
    (tests / "unit").mkdir(parents=True)
    (tests / "testlib.py").write_text("trusted helper\n")
    (tests / "unit" / "test_subject.py").write_text("def test_subject():\n    assert True\n")
    worker.git("add", "tests", cwd=git_repo)
    worker.git("commit", "-m", "test baseline", cwd=git_repo)
    original_head = worker.git("rev-parse", "HEAD", cwd=git_repo)
    (git_repo / "file.txt").write_text("caller uncommitted edit\n")
    (git_repo / "untracked").write_text("caller untracked file\n")
    output, control = tmp_path / "canary-output", tmp_path / "control"
    output.mkdir()
    control.mkdir()
    calls = []
    original_command, original_run = worker.command, subprocess.run

    def command(args, **kwargs):
        if args[0] != "docker":
            return original_command(args, **kwargs)
        calls.append(args)
        assert "--network=none" in args
        assert not any("/codex" in arg or "CODEX_HOME" in arg or "unconfined" in arg for arg in args)
        mounts = [arg for arg in args if arg.startswith("type=bind,src=")]
        checkout = Path(next(arg.split(",src=", 1)[1].split(",dst=")[0] for arg in mounts if arg.endswith("dst=/workspace")))
        baseline = Path(next(arg.split(",src=", 1)[1].split(",dst=")[0] for arg in mounts if arg.endswith("dst=/baseline,readonly")))
        assert checkout.is_relative_to(output)
        assert (checkout / ".git").is_dir()
        assert worker.git("rev-parse", "HEAD", cwd=checkout) == original_head
        assert (checkout / "file.txt").read_text() == "before\n"
        assert not (checkout / "untracked").exists()
        assert (baseline / "tests" / "testlib.py").read_text() == "trusted helper\n"
        (checkout / "file.txt").write_text("validator side effect\n")
        return "frozen_baseline passed\ncandidate passed\n2 passed\n"

    def run(args, **kwargs):
        if args[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(args, 0)
        return original_run(args, **kwargs)

    monkeypatch.setattr(worker, "command", command)
    monkeypatch.setattr(worker.subprocess, "run", run)
    monkeypatch.setenv("GH_TOKEN", "must-not-reach-canary")
    monkeypatch.delenv("AGENTIC_CODEX_HOME", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "agentic_worker.py", "canary", "--workspace", str(git_repo),
        "--control", str(control), "--directory", str(output),
    ])

    worker.main()

    assert len(calls) == 1
    assert "2 passed" in (output / "canary-validation.txt").read_text()
    assert (git_repo / "file.txt").read_text() == "caller uncommitted edit\n"
    assert (git_repo / "untracked").is_file()
    assert not (output / "result.json").exists()
    assert not (output / "change.patch").exists()
    assert not any(path.is_dir() for path in output.iterdir())


def test_candidate_failure_keeps_both_suite_reports_in_result_and_publication_evidence(worker, candidate):
    state, repo, control, output, auth, _ = candidate
    report = (
        "=== frozen_baseline output ===\n" + "early failure detail\n" * 300
        + "=== candidate output ===\n" + "later candidate detail\n" * 300
        + 'VALIDATION_SUMMARY [{"stage":"frozen_baseline","status":"failed"},'
        + '{"stage":"candidate","status":"passed"}]\n'
    )
    state["validation_error"] = worker.CommandFailure(["docker", "run"], subprocess.CompletedProcess([], 1, report, ""))

    worker.run_candidate({"kind": "issue", "validation_sha": state["base_sha"]}, repo, control, output, auth, "model")

    result = json.loads((output / "result.json").read_text())
    validation = (output / "validation.txt").read_text()
    assert result["outcome"] == "blocked"
    summary = json.loads(result["body"].splitlines()[-1])
    assert [(record["stage"], record["status"]) for record in summary] == [
        ("frozen_baseline", "failed"), ("candidate", "passed"),
    ]
    for text in (validation, worker.validation_evidence(result, validation)):
        assert "frozen_baseline" in text and "candidate" in text
        assert "early failure detail" in text and "later candidate detail" in text


@pytest.mark.parametrize("partial_output", ["partial suite output", "VALIDATION_SUMMARY "])
def test_candidate_outer_timeout_reports_incomplete_validation(worker, candidate, partial_output):
    state, repo, control, output, auth, _ = candidate
    state["validation_error"] = subprocess.TimeoutExpired("docker", 1800, output=partial_output)

    worker.run_candidate({"kind": "issue", "validation_sha": state["base_sha"]}, repo, control, output, auth, "model")

    assert "incomplete" in (output / "validation.txt").read_text().lower()
    assert json.loads((output / "result.json").read_text())["outcome"] == "blocked"


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("docker", 1800, output="canary partial output"),
    subprocess.CalledProcessError(1, "unused"),
])
def test_canary_persists_validator_failure_without_candidate_artifacts(worker, candidate, failure):
    state, repo, control, output, _, _ = candidate
    if isinstance(failure, subprocess.CalledProcessError):
        failure = worker.CommandFailure(["docker", "run"], subprocess.CompletedProcess([], 1, "canary suite failed", ""))
    state["validation_error"] = failure

    with pytest.raises(type(failure)):
        worker.validate_checkout_canary(repo, control, output)

    evidence = (output / "canary-validation.txt").read_text()
    assert evidence.startswith("FAILED:")
    assert "canary" in evidence
    assert "frozen_baseline" in evidence and "candidate" in evidence
    assert not (output / "result.json").exists()
    assert not any(path.is_dir() for path in output.iterdir())


def test_blocked_notice_fits_a_result_at_the_combined_publication_limit(worker, candidate):
    state, repo, control, output, auth, _ = candidate
    state["result"] = valid_result(body="B", pending_validation=["p" * 1000] * 49 + ["p" * 999])
    state["validation_error"] = RuntimeError("validation failed")

    worker.run_candidate({"kind": "issue", "validation_sha": state["base_sha"]}, repo, control, output, auth, "model")

    result = worker.validate_result(json.loads((output / "result.json").read_text()))
    assert result["outcome"] == "blocked"
    assert "frozen_baseline" in result["body"] and "candidate" in result["body"]


@pytest.mark.parametrize("first_unavailable", [False, True])
def test_prepare_preserves_third_failure_through_shared_unicode_budget(worker, github, monkeypatch, tmp_path, first_unavailable):
    responses, _ = github
    responses["repos/owner/repo/issues/42"]["body"] = "https://github.com/owner/repo/actions/runs/20/attempts/2"
    responses["repos/owner/repo/actions/runs/20"] = {"id": 20, "run_attempt": 3}
    responses["repos/owner/repo/actions/runs/20/attempts/2"] = {"id": 20, "run_attempt": 2}
    jobs = [{
        "id": index, "name": name, "conclusion": "failure",
        "steps": [{"name": "Test" if index == 3 else "Scan", "conclusion": "failure", "extra": "x" * 80000}],
        "metadata": "x" * 80000,
    } for index, name in enumerate(["scan (amd64)", "scan (arm64)", "runtime (arm64)"], 1)]
    original_pages = worker.pages
    job_paths = []

    def pages(path, *, key=None):
        if "/jobs?" in path:
            job_paths.append(path)
            return jobs
        return original_pages(path)

    requested = []

    def command(args):
        job_id = int(args[args.index("--job") + 1])
        requested.append(job_id)
        if job_id == 1 and first_unavailable:
            raise RuntimeError("log expired")
        diagnostic = ("CVE-2026-1234 libthing HIGH 1.0 1.1" if job_id == 1
                      else "GHSA-abcd-efgh-ijkl certifi HIGH 1.0 1.1") if job_id < 3 else (
            "FAILED tests/container/test_guacamole.py::test_guac_vnc_tunnel[relative-xdg]\n"
            "E AssertionError: Guacamole protocol error"
        )
        return "😀 setup\n" * 20000 + diagnostic + "\n" + "cleanup é\n" * 10000

    monkeypatch.setattr(worker, "pages", pages)
    monkeypatch.setattr(worker, "command", command)
    worker.prepare("issue", "42", "owner/repo", "100", tmp_path)
    evidence = json.loads((tmp_path / "task.json").read_text())["context"]["failure_evidence"]
    serialized = json.dumps(evidence)
    assert requested == [1, 2, 3]
    assert all("/attempts/2/" in path for path in job_paths)
    assert len(serialized) <= 40000
    assert "test_guac_vnc_tunnel[relative-xdg]" in serialized
    assert "Guacamole protocol error" in serialized
    assert "GHSA-abcd-efgh-ijkl" in serialized
    if not first_unavailable:
        assert "CVE-2026-1234" in serialized
    assert all(name in serialized for name in ("scan (amd64)", "scan (arm64)", "runtime (arm64)"))
    assert "extra" not in serialized and "metadata" not in serialized
    assert worker.bounded_context({"failure_evidence": evidence})["failure_evidence"] == evidence
    if first_unavailable:
        assert "Log unavailable" in serialized


def workflow_incident():
    bundle = {
        "scope": {"workflowId": 55, "headRepository": "owner/repo", "exactRef": "main", "branchScope": True},
        "sourceSHA": "a" * 40,
        "failures": [{"check": {"jobName": "runtime", "failedStepNames": ["Test"]},
                      "conclusion": "failure", "diagnosis": "b" * 64}],
    }
    record = {
        "key": hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "origin": {"runId": 20, "attempt": 2}, "bundle": bundle,
        "observations": [{"runId": 20, "attempt": 2, "sha": "a" * 40,
                          "startedAt": 1792500000000, "completedAt": 1792500600000}],
    }
    issue = {
        "number": 42, "state": "open", "labels": [], "user": {"login": "github-actions[bot]"},
        "body": "<!-- neurodesktop-workflow-failure: run=20 -->\n"
                "<!-- neurodesktop-incident:v1 " + json.dumps(record, separators=(",", ":")) + " -->\n"
                "Workflow failure https://github.com/owner/repo/actions/runs/20/attempts/2",
    }
    return issue, {"key": record["key"], "origin": record["origin"]}


def test_workflow_incident_publication_cannot_close_it(worker, proposal, publisher_api, monkeypatch):
    task, output, repo, remote = proposal
    issue, binding = workflow_incident()
    task["incident"] = binding
    original = worker.api
    monkeypatch.setattr(worker, "api", lambda path, payload=None: issue if path == "repos/owner/repo/issues/42" else original(path, payload))
    worker.write_json(output / "result.json", valid_result(
        title="Fixes #42: preserve logs",
        body="Closes #42. Fixed owner/repo#42. Resolves https://github.com/owner/repo/issues/42.",
        pending_validation=["Fixes #42 after image tests"],
    ))
    with (output / "validation.txt").open("a") as stream:
        stream.write("Closes #42\n")
    assert worker.publish(task, output, repo) == 91
    request = next(payload for path, payload in publisher_api[0] if path == "repos/owner/repo/pulls")
    published = request["body"] + request["title"] + worker.git("log", "-1", "--format=%B", task["branch"], cwd=remote)
    assert "Tracks #42" in request["body"]
    assert not any(word in published.lower() for word in ("fixes ", "fixed ", "closes ", "resolves "))
    assert "original" in request["body"].lower()


def test_publication_rechecks_workflow_incident_eligibility(worker, proposal, publisher_api, monkeypatch):
    task, output, repo, _ = proposal
    issue, binding = workflow_incident()
    task["incident"] = binding
    issue["state"] = "closed"
    original = worker.api
    monkeypatch.setattr(worker, "api", lambda path, payload=None: issue if path == "repos/owner/repo/issues/42" else original(path, payload))
    with pytest.raises(ValueError, match="incident"):
        worker.publish(task, output, repo)
    assert not publisher_api[1]


@pytest.mark.parametrize("unit", ["x", "😀"])
def test_manifest_overflow_reports_exact_job_and_step_omissions(worker, monkeypatch, unit):
    monkeypatch.setattr(worker, "api", lambda path: {"id": 20, "run_attempt": 1})
    jobs = [{"id": index, "name": f"job-{index}", "conclusion": "failure",
             "steps": [{"name": f"step-{step}-" + unit * 40, "conclusion": "failure"} for step in range(20)]}
            for index in range(50)]
    monkeypatch.setattr(worker, "pages", lambda path, key: jobs)
    read = []

    def command(args):
        read.append(args[args.index("--job") + 1])
        return "E AssertionError: concrete failure\n" + "cleanup\n" * 5000

    monkeypatch.setattr(worker, "command", command)
    evidence = worker.failure_context("owner/repo", "https://github.com/owner/repo/actions/runs/20")
    assert len(read) == 50
    assert len(json.dumps(evidence)) <= 40000
    item = evidence[0]
    assert item["jobs"]
    assert item["omitted_jobs"] == 50 - len(item["jobs"])
    assert item["omitted_steps"] == 20 * item["omitted_jobs"]
    assert item["source"].endswith("/runs/20/attempts/1")
    assert all("concrete failure" in json.dumps(log["unattributed"]["diagnostics"]) for log in item["logs"])


@pytest.mark.parametrize("spoof", ["user", "quoted", "wrong_binding", "branch_only"])
def test_incident_prepare_cannot_be_suppressed_by_spoofed_pr(worker, github, monkeypatch, tmp_path, spoof):
    responses, _ = github
    issue, binding = workflow_incident()
    responses["repos/owner/repo/issues/42"] = issue
    responses["repos/owner/repo/actions/runs/20/attempts/2"] = {"id": 20, "run_attempt": 2}
    candidate = pull(body="<!-- neurodesktop-agentic:issue:42 -->")
    if spoof == "user":
        candidate["user"] = {"login": "maintainer"}
    elif spoof == "quoted":
        candidate["body"] = "Quote\n" + candidate["body"]
    elif spoof == "wrong_binding":
        candidate["body"] += '\n<!-- neurodesktop-incident-binding:v1 {"key":"other"} -->'
    else:
        candidate["body"] = "No task marker"
    responses["repos/owner/repo/pulls?state=open&per_page=100"] = [candidate]
    original = worker.pages
    monkeypatch.setattr(worker, "pages", lambda path, **kwargs: [] if "/jobs?" in path else original(path))
    task = worker.prepare("issue", "42", "owner/repo", "100", tmp_path)
    assert not task["skip"]
    assert task["incident"] == binding


def test_incident_prepare_uses_trusted_occurrences_instead_of_pasted_urls(worker, github, monkeypatch, tmp_path):
    responses, calls = github
    issue, binding = workflow_incident()
    issue["body"] += "\nhttps://github.com/owner/repo/actions/runs/666/attempts/7"
    responses["repos/owner/repo/issues/42"] = issue
    responses["repos/owner/repo/actions/runs/20/attempts/2"] = {"id": 20, "run_attempt": 2}
    original = worker.pages
    monkeypatch.setattr(worker, "pages", lambda path, **kwargs: [] if "/jobs?" in path else original(path))
    task = worker.prepare("issue", "42", "owner/repo", "100", tmp_path)
    assert task["incident"] == binding
    assert task["context"]["failure_evidence"][0]["source"].endswith("/runs/20/attempts/2")
    assert not any("/666" in path for path, _ in calls)


@pytest.mark.parametrize("kind,interruption", [
    (kind, interruption) for kind in ("issue", "review", "review_no_change")
    for interruption in (None, "patch_response", "labels")
    if (kind, interruption) != ("review_no_change", "labels")
])
def test_persisted_incident_pr_is_nonclosing_after_publication_retry(
    worker, proposal, monkeypatch, kind, interruption,
):
    task, output, repo, remote = proposal
    issue, binding = workflow_incident()
    task["incident"] = binding
    worker.git("push", "origin", f"HEAD:refs/heads/{task['branch']}", cwd=repo)
    if kind.startswith("review"):
        task.update(kind="review", subject="91", incident_subject="42")
    body = (
        "<!-- neurodesktop-agentic:issue:42 -->\n"
        "<!-- neurodesktop-incident-binding:v1 " + json.dumps(binding) + " -->\n"
        "Original diagnosis and validation.\n\nFixes #42\nFixes #43"
    )
    pr = pull(title="[issue-fix] #42: Fixes #42: retain diagnostics", body=body,
              html_url="https://github.com/owner/repo/pull/91")
    comments, patches = {}, []
    original_command = worker.command
    interrupted = False

    def command(args, **kwargs):
        nonlocal interrupted
        if args[:2] != ["gh", "api"]:
            return original_command(args, **kwargs)
        path = args[2]
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        payload = json.loads(kwargs["data"]) if kwargs.get("data") else None
        if path == "repos/owner/repo/issues/42":
            return json.dumps(issue)
        if "/pulls?" in path:
            return json.dumps([[pr]])
        if path == "repos/owner/repo/pulls/91":
            if method == "PATCH":
                patches.append(payload)
                pr.update(payload)
                if interruption == "patch_response" and not interrupted:
                    interrupted = True
                    raise RuntimeError("Simulated lost PATCH response")
            else:
                assert method == "GET"
            return json.dumps(pr)
        if "/comments?" in path:
            return json.dumps([comments.get(path.split("?")[0], [])])
        if path.endswith("/comments"):
            comments.setdefault(path, []).append({"user": {"login": "github-actions[bot]"}, **payload})
            return "{}"
        if path.endswith("/labels"):
            if interruption == "labels" and not interrupted:
                interrupted = True
                raise RuntimeError("Simulated label failure")
            return "[]"
        raise AssertionError((path, method, payload))

    monkeypatch.setattr(worker, "command", command)
    worker.write_json(output / "result.json", valid_result(
        outcome="no_change" if kind == "review_no_change" else "change", title="Closes #42: diagnostics",
    ))
    if interruption:
        with pytest.raises(RuntimeError, match="Simulated"):
            worker.publish(task, output, repo)
    expected = None if kind == "review_no_change" else 91
    assert worker.publish(task, output, repo) == expected
    first_head = worker.git("rev-parse", task["branch"], cwd=remote)
    assert worker.publish(task, output, repo) == expected
    assert pr["body"] == body.replace("Fixes #42", "Tracks #42")
    assert pr["title"] == "[issue-fix] #42: Tracks #42: retain diagnostics"
    assert patches == [{"title": pr["title"], "body": pr["body"]}]
    assert worker.git("rev-parse", task["branch"], cwd=remote) == first_head
    assert worker.git("rev-list", "--count", f"{task['base_sha']}..{task['branch']}", cwd=remote) == ("1" if kind == "review" else "0")
    assert "Closes #42" not in worker.git("log", "-1", "--format=%B", task["branch"], cwd=remote)


@pytest.mark.parametrize("first_unavailable", [False, True])
def test_prepare_allocates_diagnostics_to_each_failed_step(worker, github, monkeypatch, tmp_path, first_unavailable):
    responses, _ = github
    responses["repos/owner/repo/issues/42"]["body"] = "https://github.com/owner/repo/actions/runs/20/attempts/2"
    responses["repos/owner/repo/actions/runs/20/attempts/2"] = {"id": 20, "run_attempt": 2}
    jobs = [{
        "id": job, "name": f"job-{job}", "conclusion": "failure",
        "steps": [
            {"number": 1, "name": "Scan", "conclusion": "failure"},
            {"number": 2, "name": "VNC", "conclusion": "failure"},
            {"number": 3, "name": "Cleanup", "conclusion": "success"},
        ],
    } for job in range(1, 5)]
    original_pages = worker.pages
    monkeypatch.setattr(worker, "pages", lambda path, **kwargs: jobs if "/jobs?" in path else original_pages(path))
    requested = []

    def command(args):
        job = int(args[args.index("--job") + 1])
        requested.append(job)
        if job == 1 and first_unavailable:
            raise RuntimeError("Log expired")
        def line(step, text):
            return f"job-{job}\t{step}\t2026-10-20T10:00:00.0000000Z {text}\n"
        return (
            line("Scan", "Error: long scan diagnostic 😀 é") * 10000
            + line("Scan", "SCAN-END")
            + line("VNC", f"Error: UNIQUE-VNC-{job} Guacamole protocol error")
            + line("VNC", f"VNC-END-{job}")
            + line("Cleanup", "cleanup é") * 10000
        )

    monkeypatch.setattr(worker, "command", command)
    worker.prepare("issue", "42", "owner/repo", "100", tmp_path)
    evidence = json.loads((tmp_path / "task.json").read_text())["context"]["failure_evidence"]
    serialized = json.dumps(evidence)
    assert requested == [1, 2, 3, 4]
    assert len(serialized) <= 40000
    for job in range(2 if first_unavailable else 1, 5):
        assert f"UNIQUE-VNC-{job}" in serialized
        record = evidence[0]["logs"][job - 1]
        assert [step["name"] for step in record["steps"]] == ["Scan", "VNC"]
        scan, vnc = record["steps"]
        assert "Error: long scan diagnostic" in json.dumps(scan["diagnostics"])
        assert "SCAN-END" in scan["tail"]
        assert f"UNIQUE-VNC-{job}" in json.dumps(vnc["diagnostics"])
        assert f"VNC-END-{job}" in vnc["tail"]
        assert "cleanup" not in vnc["tail"]
        assert record["omitted_nonfailure_lines"] == 10000
    if first_unavailable:
        assert all(step["error"] == "Log unavailable" for step in evidence[0]["logs"][0]["steps"])
    assert worker.bounded_context({"failure_evidence": evidence})["failure_evidence"] == evidence


@pytest.mark.parametrize("kind", ["issue", "review"])
@pytest.mark.parametrize("changed", ["author", "fork", "closed", "marker", "binding", "incident", "ignore"])
def test_persisted_incident_pr_rechecks_ownership_before_mutation(worker, proposal, monkeypatch, kind, changed):
    task, output, repo, remote = proposal
    issue, binding = workflow_incident()
    task["incident"] = binding
    if kind == "review":
        task.update(kind="review", subject="91", incident_subject="42")
        worker.git("push", "origin", f"HEAD:refs/heads/{task['branch']}", cwd=repo)
    original = pull(body="<!-- neurodesktop-agentic:issue:42 -->\nFixes #42")
    current = json.loads(json.dumps(original))
    if changed == "author":
        current["user"]["login"] = "maintainer"
    elif changed == "fork":
        current["head"]["repo"]["full_name"] = "other/repo"
    elif changed == "closed":
        current["state"] = "closed"
    elif changed == "marker":
        current["body"] = "<!-- neurodesktop-agentic:issue:43 -->\nFixes #42"
    elif changed == "binding":
        current["body"] = '<!-- neurodesktop-agentic:issue:42 -->\n<!-- neurodesktop-incident-binding:v1 {"key":"other"} -->\nFixes #42'
    reads = 0
    writes = []

    def api(path, payload=None, **kwargs):
        nonlocal reads
        if payload is not None:
            writes.append((path, payload))
            raise AssertionError("An ineligible PR or incident must not be mutated")
        if path == "repos/owner/repo/issues/42":
            reads += 1
            if reads > 1 and changed == "incident":
                return {**issue, "state": "closed"}
            if reads > 1 and changed == "ignore":
                return {**issue, "labels": [{"name": "agentic-ignore"}]}
            return issue
        assert path == "repos/owner/repo/pulls/91"
        return current

    monkeypatch.setattr(worker, "api", api)
    monkeypatch.setattr(worker, "pages", lambda path: [original] if "/pulls?" in path else [])
    with pytest.raises(ValueError, match="stale"):
        worker.publish(task, output, repo)
    assert writes == []
    branches = worker.git("for-each-ref", "--format=%(objectname)", f"refs/heads/{task['branch']}", cwd=remote)
    assert branches == (task["base_sha"] if kind == "review" else "")


def test_prepare_retains_distinct_failure_sections_within_timed_step(worker, github, monkeypatch, tmp_path):
    responses, _ = github
    responses["repos/owner/repo/issues/42"]["body"] = "https://github.com/owner/repo/actions/runs/20/attempts/2"
    responses["repos/owner/repo/actions/runs/20/attempts/2"] = {"id": 20, "run_attempt": 2}
    job = {"id": 1, "name": "runtime", "conclusion": "failure", "steps": [
        {"name": "Test", "number": 1, "conclusion": "failure",
         "started_at": "2026-10-20T10:00:00Z", "completed_at": "2026-10-20T10:10:00Z"},
        {"name": "Cleanup", "number": 2, "conclusion": "success",
         "started_at": "2026-10-20T10:10:00Z", "completed_at": "2026-10-20T10:11:00Z"},
    ]}
    original = worker.pages
    monkeypatch.setattr(worker, "pages", lambda path, **kwargs: [job] if "/jobs?" in path else original(path))
    prefix = "runtime\tUNKNOWN STEP\t2026-10-20T10:05:00.0000000Z "
    log = (
        prefix + "Error: FIRST-CAUSE\n"
        + (prefix + "E AssertionError: noisy early diagnostic é\n") * 4000
        + (prefix + "neutral output\n") * 10
        + prefix + "ERROR Guacamole: Non-numeric character in element length\n"
        + (prefix + "neutral output\n") * 10
        + prefix + "FAILED test_guac_vnc_tunnel[relative-xdg]\n"
        + "runtime\tUNKNOWN STEP\t2026-10-20T10:10:00.1000000Z Error: AMBIGUOUS-BOUNDARY\n"
        + "runtime\tUNKNOWN STEP\t2026-10-20T10:10:02.0000000Z cleanup\n" * 10000
        + "Error: Test mentioned in an untimed message\n"
    )
    monkeypatch.setattr(worker, "command", lambda args: log)
    worker.prepare("issue", "42", "owner/repo", "100", tmp_path)
    evidence = json.loads((tmp_path / "task.json").read_text())["context"]["failure_evidence"]
    record = evidence[0]["logs"][0]
    diagnostics = json.dumps(record["steps"][0]["diagnostics"])
    assert "FIRST-CAUSE" in diagnostics
    assert "Non-numeric character in element length" in diagnostics
    assert "FAILED test_guac_vnc_tunnel[relative-xdg]" in diagnostics
    assert "AMBIGUOUS-BOUNDARY" not in diagnostics
    assert "untimed message" not in diagnostics
    assert "AMBIGUOUS-BOUNDARY" in json.dumps(record["unattributed"])
    assert "untimed message" in json.dumps(record["unattributed"])
    assert record["omitted_nonfailure_lines"] == 10000
    assert len(json.dumps(evidence)) <= 40000
    assert worker.bounded_context({"failure_evidence": evidence})["failure_evidence"] == evidence


@pytest.mark.parametrize("started,completed", [
    (None, "2026-10-20T10:10:00Z"), ("2026-10-20T10:00:00Z", None), (None, None),
])
@pytest.mark.parametrize("prefix", ["Test", "UNKNOWN STEP"])
def test_prepare_preserves_logs_with_nullable_step_timing(worker, github, monkeypatch, tmp_path, started, completed, prefix):
    responses, _ = github
    responses["repos/owner/repo/issues/42"]["body"] = "https://github.com/owner/repo/actions/runs/20/attempts/2"
    responses["repos/owner/repo/actions/runs/20/attempts/2"] = {"id": 20, "run_attempt": 2}
    jobs = [{"id": index, "name": f"job-{index}", "conclusion": "failure", "steps": [{
        "name": "Test", "number": 1, "conclusion": "failure", "started_at": started, "completed_at": completed,
    }]} for index in (1, 2)]
    original = worker.pages
    monkeypatch.setattr(worker, "pages", lambda path, **kwargs: jobs if "/jobs?" in path else original(path))
    requested = []

    def command(args):
        job = int(args[args.index("--job") + 1])
        requested.append(job)
        return f"job-{job}\t{prefix}\t2026-10-20T10:05:00.0000000Z Error: ORIGINAL-CAUSE-{job}\n"

    monkeypatch.setattr(worker, "command", command)
    worker.prepare("issue", "42", "owner/repo", "100", tmp_path)
    evidence = json.loads((tmp_path / "task.json").read_text())["context"]["failure_evidence"]
    assert requested == [1, 2]
    for index, record in enumerate(evidence[0]["logs"], 1):
        slot = record["steps"][0] if prefix == "Test" else record["unattributed"]
        assert f"ORIGINAL-CAUSE-{index}" in json.dumps(slot["diagnostics"])
        if prefix == "UNKNOWN STEP":
            assert record["steps"][0]["diagnostics"] == []
            assert "attributed" in record["steps"][0]["error"]
