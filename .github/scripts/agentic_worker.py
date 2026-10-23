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
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import tempfile
import uuid


CATEGORIES = ("updates", "security", "dead-code", "test-coverage", "refactoring")
IMAGE = "neurodesktop-agentic:codex-0.162.1"
MAX_COMMAND_OUTPUT = 8 * 1024 * 1024
MAX_PATCH = 2 * 1024 * 1024
MAX_RESULT_BODY = 30000
MAX_RESULT_TEXT = 50000
MAX_REVIEW_COMMITS = 3
MAX_FAILURE_EVIDENCE = 40000
REVIEW_COMMIT = "[agentic-review]"
GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
FAILURE_RUN_FIELDS = (
    "id", "name", "path", "html_url", "run_attempt", "event", "status",
    "conclusion", "head_branch", "head_sha", "created_at", "updated_at",
)
FAILURE_JOB_FIELDS = (
    "id", "name", "html_url", "status", "conclusion", "started_at",
    "completed_at", "steps",
)


class CommandFailure(RuntimeError):
    def __init__(self, argv, result):
        super().__init__(f"{argv[0]} {argv[1]} exited {result.returncode}")
        self.output = result.stdout + result.stderr


def command(argv, *, cwd=None, data=None, timeout=120, env=None, binary=False):
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
                payload = data if isinstance(data, bytes) else data.encode()
                process.stdin.write(payload)
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

    stdout_bytes = bytes(captured["stdout"])
    stderr_bytes = bytes(captured["stderr"])
    if stopped_for == "timeout":
        stdout = stdout_bytes if binary else stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes if binary else stderr_bytes.decode(errors="replace")
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
    if stopped_for == "output":
        streams = " and ".join(dict.fromkeys(exceeded))
        notice = f"\n[command {streams} output exceeded {MAX_COMMAND_OUTPUT} bytes; process killed]\n".encode()
        notice = notice[:MAX_COMMAND_OUTPUT]
        stderr_bytes = stderr_bytes[:MAX_COMMAND_OUTPUT - len(notice)] + notice
        returncode = process.returncode or -signal.SIGKILL
    else:
        returncode = process.returncode
    stdout = stdout_bytes if binary else stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes if binary else stderr_bytes.decode(errors="replace")
    result = subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    if result.returncode:
        # Do not include subprocess output: a failed tool may echo credentials.
        raise CommandFailure(argv, result)
    return result.stdout


def git_environment(token=None):
    token = token or os.environ.get("GH_TOKEN")
    if not token:
        return GIT_ENV
    header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        **GIT_ENV, "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {header}",
    }


def git(*args, cwd=None):
    return command(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=cwd, env=git_environment(),
    ).rstrip("\n")


def api(path, payload=None, *, method="POST"):
    args = ["gh", "api", path]
    if payload is not None:
        args += ["--method", method, "--input", "-"]
    return json.loads(command(args, data=json.dumps(payload) if payload is not None else None))


def pages(path, *, key=None):
    return [item for page in json.loads(command(
        ["gh", "api", path, "--paginate", "--slurp"]
    )) for item in (page[key] if key is not None else page)]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def marker(task):
    return f"<!-- neurodesktop-agentic:{task['kind']}:{task['subject']} -->"


def review_marker(task):
    return f"<!-- neurodesktop-agentic-reviewed:{task['base_sha']} -->"


def has_task_marker(pr, task):
    return (pr.get("body") or "").split("\n", 1)[0] == marker(task)


def bounded_context(context):
    """Keep subject evidence ahead of metadata within an explicit prompt budget."""
    notices = []

    def trim(value, budget, path, *, keep_end=False):
        if len(json.dumps(value)) <= budget:
            return value
        notices.append(path)
        if isinstance(value, str):
            return fit_evidence_text(value, budget, keep_end=keep_end)
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
            priority = (
                "number", "title", "body", "base", "head", "path", "line",
                "diff_hunk", "logs", "job", "tail", "jobs", "run",
            )
            keys = [key for key in priority if key in value]
            keys += [key for key in value if key not in priority]
            for key in keys:
                remaining = budget - len(json.dumps(result)) - len(json.dumps(key)) - 4
                if remaining < 200:
                    break
                result[key] = trim(
                    value[key], min(20000, remaining), f"{path}.{key}",
                    keep_end=key == "tail",
                )
            return result
        return value

    result = {
        key: pack_failure_evidence(value) if key == "failure_evidence" else
        trim(value, 40000 if key != "open_pull_requests" else 10000, key)
        for key, value in context.items()
    }
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
    if len(result["title"]) > 160 or len(result["body"]) > MAX_RESULT_BODY:
        raise ValueError("Codex result exceeds publication limits")
    pending = result["pending_validation"]
    if not isinstance(pending, list) or len(pending) > 50 or not all(
        isinstance(item, str) and len(item) <= 1000 for item in pending
    ):
        raise ValueError("Invalid pending validation")
    if len(result["body"]) + sum(len(item) for item in pending) > MAX_RESULT_TEXT:
        raise ValueError("Codex result leaves insufficient room for GitHub validation evidence")
    return result


def fit_evidence_text(text, budget, *, keep_end=False):
    if len(json.dumps(text)) <= budget:
        return text
    notice = "\n[Earlier evidence truncated]\n" if keep_end else "\n[Evidence truncated]"
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = notice + text[-middle:] if keep_end else text[:middle] + notice
        if len(json.dumps(candidate)) <= budget:
            low = middle
        else:
            high = middle - 1
    return notice + (text[-low:] if low else "") if keep_end else text[:low] + notice


def compact_failure_fields(value, fields):
    result = {key: value[key] for key in fields if key in value and key != "steps"}
    for key, text in list(result.items()):
        if isinstance(text, str) and len(json.dumps(text)) > 1000:
            result[key] = fit_evidence_text(text, 1000)
    if "steps" in fields and "steps" in value:
        result["steps"] = [compact_failure_fields(step, ("name", "number", "conclusion"))
                           for step in value["steps"] if step.get("conclusion") in {"failure", "timed_out", "cancelled"}]
    return result


def diagnostic_sections(log):
    lines = log.splitlines()
    selected = set()
    for index, line in enumerate(lines):
        if re.search(r"CVE-\d{4}-\d+|GHSA-[\w-]+|FAILED\s|(?:AssertionError|Exception|Error|ERROR|FATAL|error)[:\s]|\bE\s+(?:assert|[A-Z])", line):
            selected.update(range(max(0, index - 2), min(len(lines), index + 3)))
    sections = []
    previous = -2
    for index in sorted(selected):
        if index != previous + 1:
            sections.append([])
        sections[-1].append(lines[index])
        previous = index
    return ["\n".join(section) for section in sections]


def collect_job_log(repo, job):
    steps = job.get("steps", [])
    failed = {index: {**compact_failure_fields(step, ("name", "number", "conclusion")),
                      "diagnostics": [], "tail": ""}
              for index, step in enumerate(steps) if step.get("conclusion") in {"failure", "timed_out", "cancelled"}}
    record = {"job": compact_failure_fields(job, ("name",)).get("name", "Unnamed job"),
              "job_id": job["id"], "steps": list(failed.values())}
    try:
        log = command(["gh", "run", "view", "--repo", repo, "--job", str(job["id"]), "--log"])
    except (RuntimeError, subprocess.TimeoutExpired):
        record["error"] = "Log unavailable"
        for step in failed.values():
            step["error"] = "Log unavailable"
        return record
    names = {}
    intervals = []
    for index, step in enumerate(steps):
        names.setdefault(step.get("name"), []).append(index)
        if step.get("conclusion") == "skipped":
            continue
        try:
            start = datetime.fromisoformat((step["started_at"] or "").replace("Z", "+00:00")).timestamp()
            end = datetime.fromisoformat((step["completed_at"] or "").replace("Z", "+00:00")).timestamp()
            if end >= start:
                # Actions step timestamps omit fractions; shared boundary seconds are ambiguous.
                intervals.append((index, start, end + 1))
        except (KeyError, TypeError, ValueError):
            continue
    lines_by_step = {index: [] for index in failed}
    unattributed = []
    omitted = 0
    for line in log.splitlines(keepends=True):
        parts = line.split("\t", 2)
        prefixed = len(parts) == 3 and parts[0] == job.get("name")
        text = parts[2] if prefixed else line
        matches = names.get(parts[1], []) if prefixed else []
        timestamp = re.match(r"\ufeff?(\d{4}-\d\d-\d\dT[\d:.]+Z)\s?", text)
        if not matches and timestamp and (not prefixed or parts[1] == "UNKNOWN STEP"):
            try:
                instant = datetime.fromisoformat(timestamp[1].replace("Z", "+00:00")).timestamp()
                matches = [index for index, start, end in intervals if start <= instant < end]
            except ValueError:
                pass
        if timestamp:
            text = text[timestamp.end():]
        if len(matches) != 1:
            unattributed.append(text)
        elif matches[0] in failed:
            lines_by_step[matches[0]].append(text)
        else:
            omitted += 1
    if omitted:
        record["omitted_nonfailure_lines"] = omitted
    slots = [(failed[index], lines) for index, lines in lines_by_step.items()]
    if unattributed:
        record["unattributed"] = {"notice": "Step ownership unavailable or ambiguous; no step inferred from error text."}
        slots.append((record["unattributed"], unattributed))
    for slot, lines in slots:
        text = "".join(lines)
        slot.update(diagnostics=diagnostic_sections(text), tail=text[-20000:])
        if not lines:
            slot["error"] = "No log lines could be attributed to this failed step"
        if len(text) > 20000:
            slot["omitted_tail_characters"] = len(text) - 20000
    return record


def fit_diagnostic_sections(sections, budget):
    retained = list(sections)
    while len(retained) > 1 and len(retained) * 128 > budget:
        retained.pop(len(retained) // 2)
    omitted = len(sections) - len(retained)
    remaining = budget - len(json.dumps(["" for _ in retained]))
    fitted = []
    for index, text in enumerate(retained):
        allowance = remaining // (len(retained) - index)
        if len(json.dumps(text)) - 2 <= allowance:
            fitted.append(text)
        else:
            start = fit_evidence_text(text, allowance // 2)
            end = fit_evidence_text(text, allowance - len(json.dumps(start)), keep_end=True)
            fitted.append(start + end)
        remaining -= len(json.dumps(fitted[-1])) - 2
    return fitted, omitted


def pack_failure_evidence(evidence):
    if len(json.dumps(evidence)) <= MAX_FAILURE_EVIDENCE:
        return evidence
    packed = json.loads(json.dumps(evidence))
    texts = []
    for item in packed:
        for log in item.get("logs", []):
            for slot in [*log.get("steps", []), *([log["unattributed"]] if "unattributed" in log else [])]:
                if slot.get("diagnostics") or slot.get("tail"):
                    texts.append((log, slot, slot["diagnostics"], slot["tail"]))
                    slot.update(diagnostics=[], tail="", omitted_diagnostic_sections=0)
        item["packing_notice"] = "Logs share a 40000-character JSON budget; consult the linked exact attempts for complete evidence."
    while len(json.dumps(packed)) + len(texts) * 256 > MAX_FAILURE_EVIDENCE - 32:
        item = next((entry for entry in reversed(packed) if entry.get("jobs")), None)
        if item is None:
            raise ValueError("Failure evidence metadata exceeds the shared budget")
        job = item["jobs"].pop()
        item["omitted_jobs"] = item.get("omitted_jobs", 0) + 1
        item["omitted_steps"] = item.get("omitted_steps", 0) + len(job.get("steps", []))
        logs = item.get("logs", [])
        removed = next((log for log in reversed(logs) if log.get("job_id") == job.get("id") or ("job_id" not in log and log.get("job") == job.get("name"))), None)
        if removed is not None:
            logs.remove(removed)
            texts = [entry for entry in texts if entry[0] is not removed]
    remaining = MAX_FAILURE_EVIDENCE - 32 - len(json.dumps(packed))
    for index, (_, slot, diagnostics, tail) in enumerate(texts):
        allowance = remaining // (len(texts) - index)
        before = len(json.dumps(slot))
        slot["tail"] = fit_evidence_text(tail, allowance // 3 + 2, keep_end=True)
        slot["diagnostics"], slot["omitted_diagnostic_sections"] = fit_diagnostic_sections(
            diagnostics, allowance - (len(json.dumps(slot["tail"])) - 2) - 16,
        )
        remaining -= len(json.dumps(slot)) - before
    return packed


def incident_record(issue):
    if issue.get("user", {}).get("login") != "github-actions[bot]":
        return None
    lines = (issue.get("body") or "").split("\n")
    match = re.fullmatch(r"<!-- neurodesktop-workflow-failure: run=([1-9]\d*) -->", lines[0])
    if not match:
        return None
    prefix = "<!-- neurodesktop-incident:v1 "
    if len(lines) < 2 or not lines[1].startswith("<!-- neurodesktop-incident:"):
        return {"legacyRunId": int(match[1])}
    if not lines[1].startswith(prefix) or not lines[1].endswith(" -->"):
        raise ValueError("Malformed workflow incident record")
    try:
        record = json.loads(lines[1][len(prefix):-4])
        origin = record["origin"]
        bundle = record["bundle"]
        scope = bundle["scope"]
        observations = record["observations"]
        if (not re.fullmatch(r"[a-f0-9]{64}", record["key"])
                or origin["runId"] != int(match[1]) or type(origin["attempt"]) is not int or origin["attempt"] < 1
                or not re.fullmatch(r"[a-f0-9]{40}", bundle["sourceSHA"])
                or type(scope["workflowId"]) is not int or scope["workflowId"] < 1
                or not re.fullmatch(r"[\w.-]+/[\w.-]+", scope["headRepository"])
                or not isinstance(scope["exactRef"], str) or not scope["exactRef"]
                or not ((scope.get("branchScope") is True and "prNumbers" not in scope)
                        or ("branchScope" not in scope and isinstance(scope.get("prNumbers"), list)
                            and scope["prNumbers"] and all(type(number) is int and number > 0 for number in scope["prNumbers"])))
                or not observations or not all(type(item["runId"]) is int and item["runId"] > 0
                    and type(item["attempt"]) is int and item["attempt"] > 0
                    and item["sha"] == bundle["sourceSHA"] for item in observations)
                or not any(item["runId"] == origin["runId"] and item["attempt"] == origin["attempt"] for item in observations)
                or not bundle["failures"]
                or not all(isinstance(failure["check"]["jobName"], str)
                           and isinstance(failure["check"]["failedStepNames"], list)
                           and all(isinstance(step, str) for step in failure["check"]["failedStepNames"])
                           and failure["conclusion"] in {"failure", "timed_out"}
                           and isinstance(failure["diagnosis"], str) for failure in bundle["failures"])):
            raise ValueError("Invalid record")
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError("Malformed workflow incident record") from error
    return record


def incident_binding(record):
    if record is None or "legacyRunId" in record:
        return record
    return {"key": record["key"], "origin": record["origin"]}


def incident_pull(pr, task):
    if not owned_pull(pr, task["repo"]) or pr.get("state") != "open" or not has_task_marker(pr, task):
        return False
    lines = (pr.get("body") or "").split("\n")
    prefix = "<!-- neurodesktop-incident-binding:v1 "
    if len(lines) > 1 and lines[1].startswith("<!-- neurodesktop-incident-binding:"):
        try:
            return lines[1].startswith(prefix) and lines[1].endswith(" -->") and json.loads(lines[1][len(prefix):-4]) == task["incident"]
        except ValueError:
            return False
    return True  # Legacy first-line task markers already bind this canonical issue.


def failure_context(repo, body, *, occurrences=None):
    evidence = []
    if occurrences is None:
        occurrences = [
            (int(run), int(attempt) if attempt else None)
            for run, attempt in re.findall(
                rf"https://github\.com/{re.escape(repo)}/actions/runs/(\d+)(?:/attempts/(\d+))?", body,
            )
        ]
    references = list(dict.fromkeys(occurrences))
    selected = references if len(references) <= 2 else [references[0], references[-1]]
    for run_id, attempt in selected:
        path = f"repos/{repo}/actions/runs/{run_id}"
        source = f"https://github.com/{repo}/actions/runs/{run_id}"
        try:
            run_response = api(path + (f"/attempts/{attempt}" if attempt else ""))
            if attempt and (run_response.get("run_attempt") != attempt or str(run_response.get("id")) != str(run_id)):
                raise ValueError("Exact attempt metadata unavailable")
            attempt = attempt or run_response.get("run_attempt")
            if attempt:
                path += f"/attempts/{attempt}"
                source += f"/attempts/{attempt}"
            run = compact_failure_fields(run_response, FAILURE_RUN_FIELDS)
            jobs = pages(path + "/jobs?per_page=100", key="jobs")
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            evidence.append({"run": {"id": run_id, "run_attempt": attempt, "html_url": source},
                             "error": "Run or job manifest unavailable"})
            continue
        failed = [job for job in jobs if job.get("conclusion") in {"failure", "timed_out"}]
        logs = [collect_job_log(repo, job) for job in failed]
        evidence.append({"run": run, "jobs": [compact_failure_fields(job, FAILURE_JOB_FIELDS) for job in failed],
                         "logs": logs, "source": source})
    if evidence and len(references) > len(selected):
        evidence[0]["omitted_runs"] = len(references) - len(selected)
        evidence[0]["sampling_notice"] = "Root and latest occurrences sampled; all occurrence links remain on the source issue."
    return pack_failure_evidence(evidence)


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
        "base_sha": base, "validation_sha": base, "run_id": run_id, "skip": False, "context": {},
    }
    pulls = pages(f"repos/{repo}/pulls?state=open&per_page=100")
    owned = [pr for pr in pulls if owned_pull(pr, repo)]
    if kind == "issue":
        issue = api(f"repos/{repo}/issues/{subject}")
        record = incident_record(issue)
        if record:
            task["incident"] = incident_binding(record)
        labels = {label["name"] for label in issue["labels"]}
        task["skip"] = issue["state"] != "open" or "pull_request" in issue or bool(
            labels & {"agentic-operations", "agentic-ignore"}
        ) or "<!-- neurodesktop-agentic-operations -->" in (issue.get("body") or "")
        task["skip"] |= bool(record and record.get("recovery"))
        task["skip"] |= any(
            incident_pull(pr, task) if record else (has_task_marker(pr, task) or
            pr["head"]["ref"].startswith(f"agentic/issue-{subject}-")) for pr in owned
        )
        task["branch"] = f"agentic/issue-{subject}-{run_id}"
        if not task["skip"]:
            comments = pages(f"repos/{repo}/issues/{subject}/comments?per_page=100")
            occurrences = None
            if record and "observations" in record:
                observations = sorted(record["observations"], key=lambda item: item.get("completedAt") or 0)
                occurrences = [(item["runId"], item["attempt"]) for item in observations]
            elif record:
                occurrences = [(record["legacyRunId"], None)]
                for comment in comments:
                    match = re.fullmatch(r"<!-- neurodesktop-workflow-failure-attempt: run=([1-9]\d*) attempt=([1-9]\d*) -->",
                                         (comment.get("body") or "").split("\n")[0])
                    if comment.get("user", {}).get("login") == "github-actions[bot]" and match and int(match[1]) == record["legacyRunId"]:
                        occurrences.append((int(match[1]), int(match[2])))
                if len(occurrences) > 1:
                    occurrences.pop(0)
            task["context"] = {
                "issue": issue,
                "comments": comments,
                "open_pull_requests": [{"number": p["number"], "title": p["title"]} for p in pulls],
                "failure_evidence": failure_context(repo, issue.get("body") or "", occurrences=occurrences),
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
        issue_marker = re.fullmatch(r"<!-- neurodesktop-agentic:issue:([1-9]\d*) -->", (pr.get("body") or "").split("\n")[0])
        if valid and issue_marker:
            source_issue = api(f"repos/{repo}/issues/{issue_marker[1]}")
            record = incident_record(source_issue)
            if record:
                task["incident"] = incident_binding(record)
                task["incident_subject"] = issue_marker[1]
                task["skip"] |= not incident_pull(pr, {**task, "kind": "issue", "subject": issue_marker[1]})
                task["skip"] |= source_issue.get("state") != "open" or bool(record.get("recovery"))
        task["context"] = {
            "pull_request": pr,
            "reviews": pages(f"repos/{repo}/pulls/{subject}/reviews?per_page=100"),
            "review_comments": pages(f"repos/{repo}/pulls/{subject}/comments?per_page=100"),
            "comments": pages(f"repos/{repo}/issues/{subject}/comments?per_page=100"),
        }
        task["skip"] |= not any(
            review.get("user", {}).get("login") == "coderabbitai[bot]"
            and review.get("commit_id") == task["base_sha"]
            and review.get("state") in {"COMMENTED", "CHANGES_REQUESTED", "APPROVED"}
            for review in task["context"]["reviews"]
        ) or any(
            comment.get("user", {}).get("login") == "github-actions[bot]"
            and (comment.get("body") or "").split("\n", 1)[0] == review_marker(task)
            for comment in task["context"]["comments"]
        )
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
    prefix = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    names = command(
        [*prefix, "diff", "--cached", "--name-only", "-z"],
        cwd=workspace, env=GIT_ENV, binary=True,
    ).split(b"\0")
    names = [os.fsdecode(name) for name in names if name]
    if len(names) > 50:
        raise ValueError("Patch changes more than 50 files")
    for name in names:
        if any(part in {".git", ".env"} for part in Path(name).parts) or Path(name).name in {"auth.json", "credentials.json"}:
            raise ValueError("Patch includes a credential or Git metadata path")
    patch = command(
        [*prefix, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv"],
        cwd=workspace, env=GIT_ENV, binary=True,
    )
    if len(patch) > MAX_PATCH:
        raise ValueError("Patch exceeds 2 MiB")
    if re.search(rb"^(?:new file mode|new mode) (?:120000|160000)$", patch, re.M):
        raise ValueError("New symlinks and submodules require manual changes")
    return patch if names else b""


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
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()
    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    if process.wait() != 0:
        raise ValueError("Could not snapshot base tests: " + stderr[-1000:])
    required = (destination / "tests" / "testlib.py", destination / "tests" / "unit")
    if not all(path.exists() for path in required):
        raise ValueError("Base revision does not contain the unit-test baseline")


def stage_worker_build_context(workspace, destination):
    scripts = destination / ".github" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("agentic_worker.py", "check_agentic_sandbox.py"):
        shutil.copyfile(workspace / ".github" / "scripts" / name, scripts / name)
    control = destination / "config" / "agentic"
    shutil.copytree(workspace / "config" / "agentic", control, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    lint = control / "javascript-lint"
    lint.mkdir(exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(workspace / "tools" / "javascript-lint" / name, lint / name)


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


def validation_failure_report(failure):
    details = getattr(failure, "output", None) or ""
    if isinstance(details, bytes):
        details = details.decode(errors="replace")
    reason = "validator timed out; incomplete suite report" if isinstance(failure, subprocess.TimeoutExpired) else "validator failed"
    return f"FAILED: frozen_baseline and candidate unit tests; {reason}\n" + details[-24000:]


def validate_checkout_canary(workspace, control, output):
    output.mkdir(parents=True, exist_ok=True)
    prefix = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    revision = command([*prefix, "rev-parse", "HEAD"], cwd=workspace, env=GIT_ENV).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Canary requires a trusted full HEAD revision")
    try:
        with tempfile.TemporaryDirectory(prefix="checkout-canary-", dir=output) as directory:
            temporary = Path(directory)
            checkout, baseline = temporary / "checkout", temporary / "baseline"
            command([*prefix, "clone", "--no-hardlinks", "--no-checkout", "--", str(workspace), str(checkout)], env=GIT_ENV)
            command([*prefix, "checkout", "--detach", revision], cwd=checkout, env=GIT_ENV)
            snapshot_validation_baseline(checkout, revision, baseline)
            validation = validate_candidate_patch(checkout, control, output, baseline)
    except (RuntimeError, subprocess.TimeoutExpired) as failure:
        (output / "canary-validation.txt").write_text(validation_failure_report(failure))
        raise
    (output / "canary-validation.txt").write_text(validation[-30000:])
    return validation


def run_candidate(task, workspace, control, output, auth, model):
    output.mkdir(parents=True, exist_ok=True)
    auth = auth.resolve(strict=True)
    if auth == workspace or workspace in auth.parents:
        raise ValueError("Codex authentication must be outside the checkout")
    with tempfile.TemporaryDirectory(prefix="neurodesktop-agentic-baseline-", dir=output.parent) as directory:
        baseline = Path(directory)
        # The model never receives this mount. Snapshot before it can alter the checkout.
        snapshot_validation_baseline(workspace, task["validation_sha"], baseline)
        with (auth / ".neurodesktop-worker.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = token_values(auth)
            prompt = (control / "prompt.md").read_text() + "\nTask evidence:\n" + json.dumps(task)
            if len(prompt) > 250000:
                raise ValueError("Task evidence exceeds 250000 characters; narrow the issue")
            name = f"neurodesktop-codex-{uuid.uuid4().hex}"
            (output / "container-name").write_text(name)
            args = docker_args(name, workspace, control, output, auth=auth) + subscription_guard() + [
                "sh", "-c", 'python /control/validate.py --prepare-workspace /workspace && exec "$@"', "sh",
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
            if any(secret.encode() in patch or secret in json.dumps(result) for secret in secrets):
                (output / "result.json").unlink()
                raise ValueError("Credential material detected; refusing artifacts")
            (output / "change.patch").write_bytes(patch)
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
                validation = validation_failure_report(failure)
                summary = validation.rsplit("VALIDATION_SUMMARY ", 1)
                detail = summary[-1].split("\n", 1)[0][:1500] if len(summary) == 2 else ""
                detail = detail or "frozen_baseline and candidate results unavailable; incomplete validation report."
                notice = "\n\nIndependent checkout validation failed or timed out. The candidate was not published.\n" + detail
                while len(notice) + sum(len(item) for item in result["pending_validation"]) > MAX_RESULT_TEXT:
                    result["pending_validation"].pop()
                limit = min(MAX_RESULT_BODY, MAX_RESULT_TEXT - sum(len(item) for item in result["pending_validation"]))
                result["body"] = result["body"][:limit - len(notice)] + notice
            # Tests may modify files. Publish exactly the patch inspected before tests.
            write_json(output / "result.json", result)
            proof = {"passed": result["outcome"] == "change", "patch_sha256": hashlib.sha256(patch).hexdigest()}
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


def comment_once(task, body, *, number=None, purpose="result", key=None):
    repo = task["repo"]
    number = task["subject"] if number is None else number
    key = key or f"<!-- neurodesktop-agentic-{purpose}:{task['run_id']} -->"
    comments = pages(f"repos/{repo}/issues/{number}/comments?per_page=100")
    if not any(
        c["user"]["login"] == "github-actions[bot]"
        and (c.get("body") or "").split("\n")[0] == key for c in comments
    ):
        api(f"repos/{repo}/issues/{number}/comments", {"body": key + "\n" + body})


def validation_evidence(result, validation):
    body = "\n\nIndependent validation: frozen and candidate `tests/unit` suites against submitted sources\n\n```text\n"
    excerpt = validation if len(validation) <= 12000 else validation[:6000] + "\n[Middle output omitted]\n" + validation[-6000:]
    body += excerpt.replace("```", "'''") + "\n```"
    body += "\n\nPending validation before merge:\n" + "\n".join(
        f"- {line}" for line in (result["pending_validation"] or ["Review the diff and required CI results."])
    )
    return body


def complete_publication(task, pr, result, workspace, validation):
    number = pr["number"]
    if task.get("incident"):
        reconcile_incident_pull(task, number, pr["head"]["ref"])
    api(f"repos/{task['repo']}/issues/{number}/labels", {"labels": ["agentic-workflow"]})
    published_task = {**task, "branch": pr["head"]["ref"]}
    if trigger_ci(published_task, workspace) is False:
        comment_once(task,
                     "Automatic CI was not triggered because this PR changes workflow files. "
                     "A maintainer must review those changes before running their checks.",
                     number=number, purpose="ci-review-required")
    if task["kind"] == "issue":
        proposal = "Proposed change" if task.get("incident") else "Proposed fix"
        comment_once(task, f"{proposal}: {pr['html_url']}\n\n{result['body']}")
    elif task["kind"] == "review":
        revision = git("rev-parse", "HEAD", cwd=workspace)
        comment_once(task, f"Review revision: `{revision}`\n\n{result['body']}"
                     + validation_evidence(result, validation), key=review_marker(task))
    comment_once(task, "@coderabbitai review", number=number, purpose="review-request")
    return number


def nonclosing_text(text, task):
    number = re.escape(task.get("incident_subject", task["subject"]))
    repo = re.escape(task["repo"])
    reference = rf"(?:\#{number}|{repo}\#{number}|https://github\.com/{repo}/issues/{number})(?![\w/])"
    closing = re.compile(rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b(?=(?:\s*:\s*|\s+){reference})", re.IGNORECASE)
    return closing.sub("Tracks", text)


def nonclosing_result(result, task):
    return {**result, "title": nonclosing_text(result["title"], task),
            "body": nonclosing_text(result["body"], task),
            "pending_validation": [nonclosing_text(item, task) for item in result["pending_validation"]]
            + ["Verify the original failed workflow checks; a diagnostic improvement does not establish recovery."]}


def check_publication_incident(task):
    if task["kind"] != "issue" and not task.get("incident"):
        return
    number = task.get("incident_subject", task["subject"])
    issue = api(f"repos/{task['repo']}/issues/{number}")
    record = incident_record(issue)
    if incident_binding(record) != task.get("incident"):
        raise ValueError("Workflow incident binding changed; refusing stale publication")
    if record and (issue.get("state") != "open" or record.get("recovery")
                   or {label["name"] for label in issue.get("labels", [])} & {"agentic-ignore", "agentic-operations"}):
        raise ValueError("Workflow incident changed or recovered; refusing stale publication")


def reconcile_incident_pull(task, number, branch):
    current = api(f"repos/{task['repo']}/pulls/{number}")
    issue_task = {**task, "kind": "issue", "subject": task.get("incident_subject", task["subject"])}
    if not incident_pull(current, issue_task) or current["head"]["ref"] != branch:
        raise ValueError("PR changed during publication; refusing stale publication")
    check_publication_incident(task)
    updated = {field: nonclosing_text(current[field], task) for field in ("title", "body")}
    if any(updated[field] != current[field] for field in updated):
        api(f"repos/{task['repo']}/pulls/{number}", updated, method="PATCH")


def publish(task, output, workspace):
    result = validate_result(json.loads((output / "result.json").read_text()))
    validation = (output / "validation.txt").read_text()
    repo = task["repo"]
    if result["outcome"] != "change":
        check_publication_incident(task)
        if task.get("incident"):
            result = nonclosing_result(result, task)
            if task["kind"] == "review":
                reconcile_incident_pull(task, task["subject"], task["branch"])
        if task["kind"] in {"issue", "review"}:
            comment_once(task, f"{result['outcome']}: {result['body']}",
                         key=review_marker(task) if task["kind"] == "review" else None)
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
    check_publication_incident(task)
    if task.get("incident"):
        result = nonclosing_result(result, task)
        validation = nonclosing_text(validation, task)
    pulls = [pr for pr in pages(f"repos/{repo}/pulls?state=open&per_page=100") if owned_pull(pr, repo)]
    if task["kind"] != "review":
        existing = next((pr for pr in pulls if (incident_pull(pr, task) if task.get("incident") else
                         pr["head"]["ref"] == task["branch"] or has_task_marker(pr, task))), None)
        if existing:
            return complete_publication(task, existing, result, workspace, validation)
    else:
        pr = api(f"repos/{repo}/pulls/{task['subject']}")
        issue_task = {**task, "kind": "issue", "subject": task.get("incident_subject", task["subject"])}
        if pr["state"] != "open" or not owned_pull(pr, repo) or (
            task.get("incident") and (not incident_pull(pr, issue_task) or pr["head"]["ref"] != task["branch"])
        ):
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
    check_publication_incident(task)
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
    body = marker(task) + "\n"
    if task.get("incident"):
        body += "<!-- neurodesktop-incident-binding:v1 " + json.dumps(task["incident"], separators=(",", ":")) + " -->\n"
    body += result["body"]
    body += validation_evidence(result, validation)
    if task["kind"] == "issue":
        body += f"\n\n{'Tracks' if task.get('incident') else 'Fixes'} #{task['subject']}"
        title = f"[issue-fix] #{task['subject']}: {result['title']}"
    else:
        title = f"[maintenance] {task['subject']}: {result['title']}"
    check_publication_incident(task)
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
    command(["git", "-c", "core.hooksPath=/dev/null", "push", "origin", f"HEAD:refs/heads/{branch}"],
            cwd=workspace, env=git_environment(token))


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


def fetch_revision(workspace, revision):
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Task is missing a trusted full revision SHA")
    git("fetch", "--no-tags", "--depth=1", "origin", revision, cwd=workspace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "run", "publish", "cleanup", "stage", "canary"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--control", type=Path)
    args = parser.parse_args()
    output = args.directory.resolve()
    if args.phase == "cleanup":
        cleanup(output)
    elif args.phase == "stage":
        stage_worker_build_context(args.workspace.resolve(), output)
    elif args.phase == "canary":
        if args.control is None:
            parser.error("canary requires --control")
        print(validate_checkout_canary(args.workspace.resolve(), args.control.resolve(), output))
    elif args.phase == "prepare":
        prepare(os.environ["TASK_KIND"], os.environ["TASK_SUBJECT"], os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"], output)
    else:
        task = json.loads((output / "task.json").read_text())
        fetch_revision(args.workspace.resolve(), task["base_sha"])
        if args.phase == "run":
            fetch_revision(args.workspace.resolve(), task["validation_sha"])
            git("checkout", "--detach", "--force", task["base_sha"], cwd=args.workspace.resolve())
            run_candidate(task, args.workspace.resolve(), args.control.resolve(), output,
                          Path(os.environ["AGENTIC_CODEX_HOME"]), os.environ["AGENTIC_CODEX_MODEL"])
        else:
            publish(task, output, args.workspace.resolve())


if __name__ == "__main__":
    main()
