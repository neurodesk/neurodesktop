"""Exercise agent task boundaries and publication against real Git repositories."""

from datetime import datetime, timezone
import hashlib
import json
import subprocess

import pytest

from testlib import load_source_module


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
            return datetime(2027, 1, 1, tzinfo=timezone.utc)
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
            pr = {"number": 91, "html_url": "https://github.com/owner/repo/pull/91", "user": {"login": "github-actions[bot]"}, "head": {"ref": payload["head"], "repo": {"full_name": "owner/repo"}}, "body": payload["body"]}
            created.append(pr)
            return pr
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


def test_failure_evidence_uses_only_this_repository_and_bounded_logs(worker, monkeypatch):
    calls = []
    def api(path):
        calls.append(path)
        return {"id": path.rsplit("/", 1)[-1]}
    def pages(path):
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
    assert [item["run"]["id"] for item in evidence] == ["20", "30"]
    assert all(len(item["logs"]) == 2 for item in evidence)
    assert all(len(log["tail"]) == 20000 for item in evidence for log in item["logs"])
    assert not any("attacker" in str(call) for call in calls)


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
            return {"state": "open", "head": {"sha": worker.git("rev-parse", task["branch"], cwd=remote)}}
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
            return {"state": "open", "head": {"sha": worker.git("rev-parse", task["branch"], cwd=remote)}}
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
            return {'state': 'open', 'head': {'sha': worker.git('rev-parse', task['branch'], cwd=remote)}}
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
