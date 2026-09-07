"""Subscription Codex execution and deterministic draft publication."""

import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tarfile
import threading
import time
import tempfile
import uuid


CATEGORIES = ("updates", "security", "dead-code", "test-coverage", "refactoring")
IMAGE = "neurodesktop-agentic:codex-0.153.4"
MAX_COMMAND_OUTPUT = 8 * 1024 * 1024
MAX_PATCH = 2 * 1024 * 1024
MAX_REVIEW_COMMITS = 3
REVIEW_COMMIT = "[agentic-review]"
GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


class CommandFailure(RuntimeError):
    def __init__(self, argv, result):
        super().__init__(f"{argv[0]} {argv[1]} exited {result.returncode}")
        self.output = result.stdout + result.stderr


def command(argv, *, cwd=None, data=None, timeout=120, env=None):
    process = subprocess.Popen(
        argv, cwd=cwd, stdin=subprocess.PIPE if data is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, **(env or {})}, start_new_session=True,
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = []
    output_limit_reached = threading.Event()

    def read_stream(name, stream):
        try:
            while chunk := os.read(stream.fileno(), 64 * 1024):
                remaining = MAX_COMMAND_OUTPUT - len(captured[name])
                if remaining > 0:
                    captured[name].extend(chunk[:remaining])
                if len(chunk) > max(0, remaining):
                    exceeded.append(name)
                    output_limit_reached.set()
                    return
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    readers = [
        threading.Thread(target=read_stream, args=(name, stream), daemon=True)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    for reader in readers:
        reader.start()

    writer = None
    if data is not None:
        def write_stdin():
            try:
                process.stdin.write(data.encode())
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

        writer = threading.Thread(target=write_stdin, daemon=True)
        writer.start()

    deadline = None if timeout is None else time.monotonic() + timeout
    stopped_for = None
    while process.poll() is None or any(reader.is_alive() for reader in readers):
        if output_limit_reached.is_set():
            stopped_for = "output"
            break
        if deadline is not None and time.monotonic() >= deadline:
            stopped_for = "timeout"
            break
        time.sleep(0.01)

    if stopped_for is None and output_limit_reached.is_set():
        stopped_for = "output"
    if stopped_for:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            if process.poll() is None:
                process.kill()
    process.wait()
    for reader in readers:
        reader.join(timeout=1)
    if writer is not None:
        writer.join(timeout=1)

    stdout = bytes(captured["stdout"]).decode(errors="replace")
    stderr = bytes(captured["stderr"]).decode(errors="replace")
    if stopped_for == "timeout":
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
    if stopped_for == "output":
        streams = " and ".join(dict.fromkeys(exceeded))
        notice = f"\n[command {streams} output exceeded {MAX_COMMAND_OUTPUT} bytes; process killed]\n"
        stderr = (stderr.encode()[:max(0, MAX_COMMAND_OUTPUT - len(notice.encode()))]
                  + notice.encode()).decode(errors="replace")
        returncode = process.returncode or -signal.SIGKILL
    else:
        returncode = process.returncode
    result = subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    if result.returncode:
        # Do not include subprocess output: a failed tool may echo credentials.
        raise CommandFailure(argv, result)
    return result.stdout


def git(*args, cwd=None):
    return command(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=cwd, env=GIT_ENV,
    ).rstrip("\n")


def api(path, payload=None):
    args = ["gh", "api", path]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    return json.loads(command(args, data=json.dumps(payload) if payload is not None else None))


def pages(path):
    return [item for page in json.loads(command(
        ["gh", "api", path, "--paginate", "--slurp"]
    )) for item in page]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def marker(task):
    return f"<!-- neurodesktop-agentic:{task['kind']}:{task['subject']} -->"


def has_task_marker(pr, task):
    return (pr.get("body") or "").split("\n", 1)[0] == marker(task)


def bounded_context(context):
    """Keep subject evidence ahead of metadata within an explicit prompt budget."""
    notices = []

    def trim(value, budget, path):
        if len(json.dumps(value)) <= budget:
            return value
        notices.append(path)
        if isinstance(value, str):
            # Reserve twelve JSON characters for escaped surrogate pairs.
            return value[:max(0, (budget - 100) // 12)] + "\n[Evidence truncated]"
        if isinstance(value, list):
            result = []
            # Retain recent discussion; the issue itself is a separate section.
            for item in reversed(value):
                remaining = budget - len(json.dumps(result)) - 2
                if remaining < 200:
                    break
                result.insert(0, trim(item, min(8000, remaining), path))
            return result
        if isinstance(value, dict):
            result = {}
            priority = ("number", "title", "body", "base", "head", "path", "line", "diff_hunk", "tail")
            keys = [key for key in priority if key in value]
            keys += [key for key in value if key not in priority]
            for key in keys:
                remaining = budget - len(json.dumps(result)) - len(json.dumps(key)) - 4
                if remaining < 200:
                    break
                result[key] = trim(value[key], min(20000, remaining), path)
            return result
        return value

    result = {key: trim(value, 40000 if key != "open_pull_requests" else 10000, key)
              for key, value in context.items()}
    if notices:
        result["truncation_notice"] = (
            "Evidence was shortened to fit the task: " + ", ".join(dict.fromkeys(notices))
            + ". Recent discussion and primary subject fields take priority. Consult the linked sources for omitted details."
        )
    return result


def owned_pull(pr, repo):
    return pr.get("user", {}).get("login") == "github-actions[bot]" and (
        (pr.get("head", {}).get("repo") or {}).get("full_name", "").lower() == repo.lower()
    )


def validate_result(result):
    if not isinstance(result, dict) or set(result) != {
        "outcome", "title", "body", "pending_validation"
    }:
        raise ValueError("Invalid Codex result fields")
    if result["outcome"] not in {"change", "no_change", "blocked"}:
        raise ValueError("Invalid Codex outcome")
    if not all(isinstance(result[k], str) and result[k].strip() for k in ("title", "body")):
        raise ValueError("Codex title and body must be nonempty strings")
    if len(result["title"]) > 160 or len(result["body"]) > 30000:
        raise ValueError("Codex result exceeds publication limits")
    pending = result["pending_validation"]
    if not isinstance(pending, list) or len(pending) > 50 or not all(
        isinstance(item, str) and len(item) <= 1000 for item in pending
    ):
        raise ValueError("Invalid pending validation")
    if len(result["body"]) + sum(len(item) for item in pending) > 50000:
        raise ValueError("Codex result leaves insufficient room for GitHub validation evidence")
    return result


def failure_context(repo, body):
    evidence = []
    # Issue URLs are data. Only numeric run IDs from this repository are used.
    for run_id in list(dict.fromkeys(re.findall(
        rf"https://github\.com/{re.escape(repo)}/actions/runs/(\d+)", body
    )))[:2]:
        run = api(f"repos/{repo}/actions/runs/{run_id}")
        jobs = pages(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
        failed = [j for j in jobs if j["conclusion"] in {"failure", "timed_out"}]
        logs = []
        for job in failed[:2]:
            try:
                log = command(["gh", "run", "view", "--repo", repo, "--job", str(job["id"]), "--log"])
                logs.append({"job": job["name"], "tail": log[-20000:]})
            except (RuntimeError, subprocess.TimeoutExpired):
                logs.append({"job": job["name"], "error": "Log unavailable"})
        evidence.append({"run": run, "jobs": failed, "logs": logs})
    return evidence


def prepare(kind, subject, repo, run_id, output):
    if kind not in {"issue", "maintenance", "review"}:
        raise ValueError("Unknown task kind")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Invalid repository")
    if not run_id.isdigit():
        raise ValueError("Invalid workflow run ID")
    if kind == "maintenance":
        if subject not in CATEGORIES:
            raise ValueError("Unknown maintenance category")
    elif not subject.isdigit() or int(subject) < 1:
        raise ValueError("Issue or PR number must be positive")
    metadata = api(f"repos/{repo}")
    branch = metadata["default_branch"]
    base = api(f"repos/{repo}/commits/{branch}")["sha"]
    task = {
        "kind": kind, "subject": subject, "repo": repo, "base_branch": branch,
        "base_sha": base, "run_id": run_id, "skip": False, "context": {},
    }
    pulls = pages(f"repos/{repo}/pulls?state=open&per_page=100")
    owned = [pr for pr in pulls if owned_pull(pr, repo)]
    if kind == "issue":
        issue = api(f"repos/{repo}/issues/{subject}")
        labels = {label["name"] for label in issue["labels"]}
        task["skip"] = issue["state"] != "open" or "pull_request" in issue or bool(
            labels & {"agentic-operations", "agentic-ignore"}
        ) or "<!-- neurodesktop-agentic-operations -->" in (issue.get("body") or "")
        task["skip"] |= any(
            has_task_marker(pr, task) or
            pr["head"]["ref"].startswith(f"agentic/issue-{subject}-") for pr in owned
        )
        task["branch"] = f"agentic/issue-{subject}-{run_id}"
        if not task["skip"]:
            task["context"] = {
                "issue": issue,
                "comments": pages(f"repos/{repo}/issues/{subject}/comments?per_page=100"),
                "open_pull_requests": [{"number": p["number"], "title": p["title"]} for p in pulls],
                "failure_evidence": failure_context(repo, issue.get("body") or ""),
            }
    elif kind == "maintenance":
        week = datetime.now(timezone.utc).strftime("%G-W%V")
        task["branch"] = f"agentic/maintenance-{subject}-{week}"
        task["skip"] = any(p["head"]["ref"].startswith(f"agentic/maintenance-{subject}-") for p in owned)
        # A manual retry in the same week must not recreate a closed proposal.
        previous = api(f"repos/{repo}/pulls?state=closed&head={metadata['owner']['login']}:{task['branch']}&per_page=1")
        task["skip"] |= any(owned_pull(pr, repo) for pr in previous)
        task["context"] = {"category": subject, "week": week}
    else:
        pr = api(f"repos/{repo}/pulls/{subject}")
        valid = (
            pr["state"] == "open" and pr["user"]["login"] == "github-actions[bot]"
            and (pr["head"].get("repo") or {}).get("full_name", "").lower() == repo.lower()
            and pr["head"]["ref"].startswith("agentic/")
            and "agentic-workflow" in {label["name"] for label in pr["labels"]}
        )
        commits = pages(f"repos/{repo}/pulls/{subject}/commits?per_page=100")
        task["skip"] = not valid or sum(
            c["commit"]["message"].startswith(REVIEW_COMMIT) for c in commits
        ) >= MAX_REVIEW_COMMITS
        task["branch"] = pr["head"]["ref"]
        task["base_sha"] = pr["head"]["sha"]
        task["context"] = {
            "pull_request": pr,
            "reviews": pages(f"repos/{repo}/pulls/{subject}/reviews?per_page=100"),
            "review_comments": pages(f"repos/{repo}/pulls/{subject}/comments?per_page=100"),
            "comments": pages(f"repos/{repo}/issues/{subject}/comments?per_page=100"),
        }
    task["context"] = bounded_context(task["context"])
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "task.json", task)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"skip={str(task['skip']).lower()}\nbase_sha={task['base_sha']}\n")
    return task


def docker_args(name, workspace, control, output, *, auth=None, baseline=None):
    args = [
        "docker", "run", "--rm", "--name", name, "--init", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
        "--pids-limit=512", "--memory=12g", "--cpus=4",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=2g,mode=1777",
        "--env", "HOME=/tmp/home", "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--mount", f"type=bind,src={workspace},dst=/workspace",
        "--mount", f"type=bind,src={workspace / '.git'},dst=/workspace/.git,readonly",
        "--mount", f"type=bind,src={control},dst=/control,readonly",
        "--mount", f"type=bind,src={output},dst=/output" + (",readonly" if auth is None else ""),
        "--workdir", "/workspace",
    ]
    if baseline is not None:
        args += ["--mount", f"type=bind,src={baseline},dst=/baseline,readonly"]
    if auth is None:
        args += ["--network=none"]
    else:
        # Codex's Linux sandbox creates namespaces. The outer container still
        # drops capabilities, host mounts, devices and the Docker socket.
        args += ["--interactive", "--security-opt=seccomp=unconfined",
                 "--security-opt=apparmor=unconfined", "--env", "CODEX_HOME=/codex",
                 "--mount", f"type=bind,src={auth},dst=/codex"]
    return args + [IMAGE, "sh", "-c", 'mkdir -p "$HOME"; exec "$@"', "sh"]


def permission_args():
    settings = [
        'default_permissions="worker"', 'approval_policy="never"',
        'permissions.worker.filesystem={":root"="read", "/workspace"="write", '
        '"/workspace/.git"="read", "/tmp"="write", "/codex"="deny", '
        '"/proc"="deny", "/output"="deny"}',
        'permissions.worker.network.enabled=false',
        'features.hooks=false', 'features.multi_agent=false',
        'projects={"/workspace"={trust_level="untrusted"}}',
    ]
    return [part for setting in settings for part in ("-c", setting)]


def subscription_guard():
    # The container retains this lock if its Actions runner process disappears.
    return ["flock", "--nonblock", "--close", "/codex/.neurodesktop-execution.lock",
            "timeout", "--signal=TERM", "--kill-after=30s", "5350s"]


def token_values(auth):
    data = json.loads((auth / "auth.json").read_text())
    if data.get("auth_mode") != "chatgpt" or not data.get("tokens"):
        raise ValueError("Worker needs a dedicated ChatGPT subscription login")
    return [value for value in data["tokens"].values() if isinstance(value, str) and len(value) >= 32]


def check_patch(workspace):
    names = git("diff", "--cached", "--name-only", "-z", cwd=workspace).split("\0")
    names = [name for name in names if name]
    if len(names) > 50:
        raise ValueError("Patch changes more than 50 files")
    for name in names:
        if any(part in {".git", ".env"} for part in Path(name).parts) or Path(name).name in {"auth.json", "credentials.json"}:
            raise ValueError("Patch includes a credential or Git metadata path")
    patch = git("diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", cwd=workspace) + "\n"
    if len(patch.encode()) > MAX_PATCH:
        raise ValueError("Patch exceeds 2 MiB")
    if re.search(r"^(?:new file mode|new mode) (?:120000|160000)$", patch, re.M):
        raise ValueError("New symlinks and submodules require manual changes")
    return patch if names else ""


def snapshot_validation_baseline(workspace, base_sha, destination):
    """Extract the base revision's tests without passing archive bytes through text."""
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("Task is missing a trusted full base SHA")
    destination.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "archive", "--format=tar", base_sha, "tests"],
        cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, **GIT_ENV},
    )
    try:
        assert process.stdout is not None
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Base test archive contains an unsafe path")
                target = destination / path
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    assert source is not None
                    with source, target.open("wb") as stream:
                        while chunk := source.read(64 * 1024):
                            stream.write(chunk)
                else:
                    raise ValueError("Base test archive contains a non-regular file")
    finally:
        if process.stdout is not None:
            process.stdout.close()
    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    if process.wait() != 0:
        raise ValueError("Could not snapshot base tests: " + stderr[-1000:])
    required = (destination / "tests" / "testlib.py", destination / "tests" / "unit")
    if not all(path.exists() for path in required):
        raise ValueError("Base revision does not contain the unit-test baseline")


def validate_candidate_patch(workspace, control, output, baseline):
    name = f"neurodesktop-check-{uuid.uuid4().hex}"
    (output / "container-name").write_text(name)
    try:
        return command(docker_args(
            name, workspace, control, output, baseline=baseline
        ) + [
            "python", "/control/validate.py", "--baseline", "/baseline",
            "--workspace", "/workspace",
        ], timeout=1800)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


def run_candidate(task, workspace, control, output, auth, model):
    output.mkdir(parents=True, exist_ok=True)
    auth = auth.resolve(strict=True)
    if auth == workspace or workspace in auth.parents:
        raise ValueError("Codex authentication must be outside the checkout")
    with tempfile.TemporaryDirectory(prefix="neurodesktop-agentic-baseline-", dir=output.parent) as directory:
        baseline = Path(directory)
        # The model never receives this mount. Snapshot before it can alter the checkout.
        snapshot_validation_baseline(workspace, task["base_sha"], baseline)
        with (auth / ".neurodesktop-worker.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = token_values(auth)
            prompt = (control / "prompt.md").read_text() + "\nTask evidence:\n" + json.dumps(task)
            if len(prompt) > 250000:
                raise ValueError("Task evidence exceeds 250000 characters; narrow the issue")
            name = f"neurodesktop-codex-{uuid.uuid4().hex}"
            (output / "container-name").write_text(name)
            args = docker_args(name, workspace, control, output, auth=auth) + subscription_guard() + [
                "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                "--strict-config", "--model", model,
                "-c", 'forced_login_method="chatgpt"',
                "-c", 'cli_auth_credentials_store="file"',
                "-c", 'model_reasoning_effort="high"',
                "-c", 'web_search="live"',
                "--output-schema", "/control/result.schema.json",
                "--output-last-message", "/output/result.json",
            ] + permission_args() + ["-"]
            try:
                command(args, data=prompt, timeout=5400)
            finally:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            secrets += token_values(auth)
            result = validate_result(json.loads((output / "result.json").read_text()))
            git("add", "--all", cwd=workspace)
            patch = check_patch(workspace)
            if any(secret in patch or secret in json.dumps(result) for secret in secrets):
                (output / "result.json").unlink()
                raise ValueError("Credential material detected; refusing artifacts")
            (output / "change.patch").write_text(patch)
        if result["outcome"] == "change":
            if not patch:
                raise ValueError("Codex requested a PR without a patch")
            # Validate only the submitted patch, without ignored files or other
            # untracked state left by the model's commands.
            git("reset", "--hard", "HEAD", cwd=workspace)
            git("clean", "-fdx", cwd=workspace)
            git("apply", "--index", str((output / "change.patch").resolve()), cwd=workspace)
            try:
                validation = validate_candidate_patch(workspace, control, output, baseline)
            except (RuntimeError, subprocess.TimeoutExpired) as failure:
                result["outcome"] = "blocked"
                result["body"] += "\n\nIndependent checkout validation failed or timed out. The candidate was not published."
                details = getattr(failure, "output", None) or ""
                if isinstance(details, bytes):
                    details = details.decode(errors="replace")
                validation = "FAILED: immutable baseline and candidate unit tests\n" + details[-24000:]
            # Tests may modify files. Publish exactly the patch inspected before tests.
            write_json(output / "result.json", result)
            proof = {"passed": result["outcome"] == "change", "patch_sha256": hashlib.sha256(patch.encode()).hexdigest()}
            (output / "validation.txt").write_text(json.dumps(proof) + "\n" + validation[-30000:])
        else:
            (output / "validation.txt").write_text("No change submitted for independent validation.\n")


def cleanup(output):
    path = output / "container-name"
    if path.exists():
        name = path.read_text()
        if not re.fullmatch(r"neurodesktop-(?:codex|check)-[a-f0-9]{32}", name):
            raise ValueError("Invalid cleanup container name")
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


def comment_once(task, body, *, number=None, purpose="result"):
    repo = task["repo"]
    number = task["subject"] if number is None else number
    key = f"<!-- neurodesktop-agentic-{purpose}:{task['run_id']} -->"
    comments = pages(f"repos/{repo}/issues/{number}/comments?per_page=100")
    if not any(
        c["user"]["login"] == "github-actions[bot]"
        and (c.get("body") or "").split("\n")[0] == key for c in comments
    ):
        api(f"repos/{repo}/issues/{number}/comments", {"body": key + "\n" + body})


def validation_evidence(result, validation):
    body = "\n\nIndependent validation: immutable baseline and candidate `tests/unit` suites\n\n```text\n"
    body += validation[-6000:].replace("```", "'''") + "\n```"
    body += "\n\nPending validation before merge:\n" + "\n".join(
        f"- {line}" for line in (result["pending_validation"] or ["Review the diff and required CI results."])
    )
    return body


def complete_publication(task, pr, result, workspace, validation):
    number = pr["number"]
    api(f"repos/{task['repo']}/issues/{number}/labels", {"labels": ["agentic-workflow"]})
    published_task = {**task, "branch": pr["head"]["ref"]}
    if trigger_ci(published_task, workspace) is False:
        comment_once(task,
                     "Automatic CI was not triggered because this PR changes workflow files. "
                     "A maintainer must review those changes before running their checks.",
                     number=number, purpose="ci-review-required")
    if task["kind"] == "issue":
        comment_once(task, f"Proposed fix: {pr['html_url']}\n\n{result['body']}")
    elif task["kind"] == "review":
        revision = git("rev-parse", "HEAD", cwd=workspace)
        comment_once(task, f"Review revision: `{revision}`\n\n{result['body']}"
                     + validation_evidence(result, validation))
    comment_once(task, "@coderabbitai review", number=number, purpose="review-request")
    return number


def publish(task, output, workspace):
    result = validate_result(json.loads((output / "result.json").read_text()))
    validation = (output / "validation.txt").read_text()
    repo = task["repo"]
    if result["outcome"] != "change":
        if task["kind"] in {"issue", "review"}:
            comment_once(task, f"{result['outcome']}: {result['body']}")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
                stream.write(f"{result['outcome']}: {result['body']}\n")
        return None
    patch = output / "change.patch"
    if not patch.is_file() or not 0 < patch.stat().st_size <= MAX_PATCH:
        raise ValueError("Missing or oversized patch")
    if "FAILED:" in validation or re.search(r"\b[1-9]\d* (?:failed|errors?)\b", validation) or not re.search(r"\b\d+ passed\b", validation):
        raise ValueError("Independent validation did not pass")
    proof = json.loads(validation.splitlines()[0])
    if proof != {"passed": True, "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest()}:
        raise ValueError("Validation does not match the candidate patch")
    pulls = [pr for pr in pages(f"repos/{repo}/pulls?state=open&per_page=100") if owned_pull(pr, repo)]
    if task["kind"] != "review":
        existing = next((pr for pr in pulls if pr["head"]["ref"] == task["branch"] or has_task_marker(pr, task)), None)
        if existing:
            return complete_publication(task, existing, result, workspace, validation)
    else:
        pr = api(f"repos/{repo}/pulls/{task['subject']}")
        if pr["state"] != "open":
            raise ValueError("PR changed during review; refusing stale publication")
    git("checkout", "-B", task["branch"], task["base_sha"], cwd=workspace)
    git("apply", "--index", str(patch.resolve()), cwd=workspace)
    git("diff", "--cached", "--check", cwd=workspace)
    # Confirm artifact paths/modes again in a fresh checkout on the publisher.
    check_patch(workspace)
    git("config", "user.name", "github-actions[bot]", cwd=workspace)
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=workspace)
    prefix = REVIEW_COMMIT if task["kind"] == "review" else "[agentic]"
    workflow_changes = bool(git("diff", "--cached", workflow_base(task, workspace),
                               "--", ".github/workflows", cwd=workspace))
    commit_args = ["commit", "-m", f"{prefix} {result['title']}"]
    if workflow_changes and os.environ.get("AGENTIC_PUSH_TOKEN"):
        commit_args += ["-m", "[skip ci]"]
    git(*commit_args, cwd=workspace)
    remote = git("ls-remote", "origin", f"refs/heads/{task['branch']}", cwd=workspace)
    if remote:
        git("fetch", "origin", task["branch"], cwd=workspace)
        remote_sha = git("rev-parse", "FETCH_HEAD", cwd=workspace)
        if remote_sha == task["base_sha"]:
            push_branch(task["branch"], workspace)
        elif git("rev-parse", "FETCH_HEAD^{tree}", cwd=workspace) == git("rev-parse", "HEAD^{tree}", cwd=workspace):
            git("reset", "--hard", "FETCH_HEAD", cwd=workspace)
        else:
            raise ValueError("Branch changed during publication; refusing stale publication")
    else:
        if task["kind"] == "review":
            raise ValueError("PR branch disappeared during review")
        if workflow_changes and os.environ.get("AGENTIC_PUSH_TOKEN"):
            # Suppress create-event workflows before updating this ref with the
            # token that can publish workflow changes. skip-ci covers only push/PR.
            git("push", "origin", f"{task['base_sha']}:refs/heads/{task['branch']}", cwd=workspace)
        push_branch(task["branch"], workspace)
    if task["kind"] == "review":
        return complete_publication(task, {
            **pr, "number": int(task["subject"]),
            "head": {**pr["head"], "ref": task["branch"]},
        }, result, workspace, validation)
    body = marker(task) + "\n" + result["body"]
    body += validation_evidence(result, validation)
    if task["kind"] == "issue":
        body += f"\n\nFixes #{task['subject']}"
        title = f"[issue-fix] #{task['subject']}: {result['title']}"
    else:
        title = f"[maintenance] {task['subject']}: {result['title']}"
    pr = api(f"repos/{repo}/pulls", {
        "title": title, "body": body, "head": task["branch"],
        "base": task["base_branch"], "draft": True,
    })
    return complete_publication(task, pr, result, workspace, validation)


def push_branch(branch, workspace, token=None):
    token = token or os.environ.get("AGENTIC_PUSH_TOKEN")
    if not token:
        git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=workspace)
        return
    header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    command(["git", "-c", "core.hooksPath=/dev/null", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=workspace, env={
        **GIT_ENV, "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {header}",
    })


def workflow_base(task, workspace):
    base = task.get("context", {}).get("pull_request", {}).get("base", {}).get("sha", task["base_sha"])
    if base != task["base_sha"]:
        git("fetch", "origin", base, cwd=workspace)
    return base


def trigger_ci(task, workspace):
    token = os.environ.get("AGENTIC_CI_TOKEN")
    if not token:
        return
    base = workflow_base(task, workspace)
    message = f"[agentic-ci] Run pull request checks ({task['run_id']})"
    git("fetch", "origin", task["branch"], cwd=workspace)
    if git("diff", "--name-only", base, "FETCH_HEAD", "--", ".github/workflows", cwd=workspace):
        return False
    if message in git("log", "FETCH_HEAD", "--format=%s", cwd=workspace).splitlines():
        return
    git("checkout", "-B", task["branch"], "FETCH_HEAD", cwd=workspace)
    git("config", "user.name", "github-actions[bot]", cwd=workspace)
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=workspace)
    git("commit", "--allow-empty", "-m", message, cwd=workspace)
    push_branch(task["branch"], workspace, token=token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "run", "publish", "cleanup"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--control", type=Path)
    args = parser.parse_args()
    output = args.directory.resolve()
    if args.phase == "cleanup":
        cleanup(output)
    elif args.phase == "prepare":
        prepare(os.environ["TASK_KIND"], os.environ["TASK_SUBJECT"], os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"], output)
    else:
        task = json.loads((output / "task.json").read_text())
        if args.phase == "run":
            run_candidate(task, args.workspace.resolve(), args.control.resolve(), output,
                          Path(os.environ["AGENTIC_CODEX_HOME"]), os.environ["AGENTIC_CODEX_MODEL"])
        else:
            publish(task, output, args.workspace.resolve())


if __name__ == "__main__":
    main()
