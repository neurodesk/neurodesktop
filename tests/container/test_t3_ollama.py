"""Real T3 first-use discovery and completion through the quiet image launcher."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest
import websocket

from testlib import resolve_source


T3 = os.environ.get("NEURODESKTOP_TEST_T3_BIN", "/opt/t3-code/node_modules/.bin/t3")
LAUNCHER = os.environ.get(
    "NEURODESKTOP_TEST_OPENCODE_LAUNCHER", "/opt/neurodesktop/t3-provider-bin/opencode"
)
DEFAULTS = resolve_source(
    "/opt/jovyan_defaults/.config/opencode/opencode.json",
    "config/agents/opencode_config.json",
)


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def isolated_environment(home):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("T3", "OPENCODE_", "XDG_"))
    }
    environment.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "T3CODE_LOG_LEVEL": "Warn",
            "T3CODE_TRACE_FILE": "/dev/null",
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
            "NEURODESK_API_KEY": "test-neurodesk-private",
            "BR_MCP_TOKEN": "test-br-private",
        }
    )
    return environment


def stop_process(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


class T3Rpc:
    def __init__(self, connection):
        self.connection = connection
        self.sequence = 0

    def call(self, method, payload):
        self.sequence += 1
        identifier = str(self.sequence)
        self.connection.send(
            json.dumps(
                {
                    "_tag": "Request",
                    "id": identifier,
                    "tag": method,
                    "payload": payload,
                    "headers": [],
                }
            )
        )
        while True:
            message = json.loads(self.connection.recv())
            if message.get("_tag") == "Ping":
                self.connection.send(json.dumps({"_tag": "Pong"}))
                continue
            if message.get("requestId") != identifier:
                continue
            assert message["_tag"] == "Exit", message
            outcome = message["exit"]
            assert outcome["_tag"] == "Success", outcome
            return outcome.get("value")


@contextmanager
def running_t3(home, ollama_host, *, inline=None):
    home.mkdir(parents=True, exist_ok=True)
    config = home / ".config/opencode/opencode.json"
    config.parent.mkdir(parents=True)
    shutil.copy(DEFAULTS, config)
    saved_config = config.read_bytes()
    environment = isolated_environment(home)
    environment["OLLAMA_HOST"] = ollama_host
    if inline is not None:
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline)
    settings = home / ".t3/userdata/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "enableProviderUpdateChecks": False,
                "providerInstances": {
                    "codex": {"driver": "codex", "enabled": False, "config": {}},
                    "claudeAgent": {
                        "driver": "claudeAgent",
                        "enabled": False,
                        "config": {},
                    },
                    "opencode": {
                        "driver": "opencode",
                        "enabled": True,
                        "config": {"binaryPath": LAUNCHER},
                    },
                },
            }
        )
    )
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    log = home / "t3.log"
    connection = None
    with log.open("w") as stream:
        process = subprocess.Popen(
            [
                T3,
                "serve",
                "--mode",
                "web",
                "--no-browser",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--base-dir",
                str(home / ".t3"),
                str(home),
            ],
            env=environment,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            assert process.poll() is None, log.read_text()[-2000:]
            try:
                with urllib.request.urlopen(
                    origin + "/.well-known/t3/environment", timeout=0.5
                ) as response:
                    descriptor = json.load(response)
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        else:
            pytest.fail("T3 did not answer its environment endpoint")
        assert descriptor["serverVersion"] == "0.1.0"
        credential = subprocess.check_output(
            [
                T3,
                "auth",
                "session",
                "issue",
                "--base-dir",
                str(home / ".t3"),
                "--ttl",
                "5m",
                "--token-only",
            ],
            env=environment,
            text=True,
            timeout=30,
        ).strip()
        connection = websocket.create_connection(
            f"ws://127.0.0.1:{port}/ws?orchestrationProtocol={descriptor['orchestrationProtocolVersion']}",
            header={"Authorization": f"Bearer {credential}"},
            timeout=90,
        )
        yield T3Rpc(connection)
        assert config.read_bytes() == saved_config
    finally:
        if connection is not None:
            connection.close()
        stop_process(process)


@contextmanager
def fake_ollama(completion_started=None, completion_release=None):
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({"models": [{"name": "neurodesk-controlled:latest"}]})

        def do_POST(self):
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )
            calls.append((self.path, dict(self.headers), payload))
            if self.path == "/api/show":
                self.reply({"model_info": {"llama.context_length": 65536}})
            elif self.path == "/v1/chat/completions":
                assert payload["model"] == "neurodesk-controlled:latest", payload
                if completion_started is not None:
                    completion_started.set()
                    assert completion_release.wait(timeout=30)
                if payload.get("stream"):
                    chunks = [
                        {
                            "id": "fixture",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": payload["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "content": "NEURODESK_OLLAMA_OK",
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        },
                        {
                            "id": "fixture",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": payload["model"],
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "stop"}
                            ],
                            "usage": {
                                "prompt_tokens": 100,
                                "completion_tokens": 8,
                                "total_tokens": 108,
                            },
                        },
                    ]
                    body = (
                        "".join("data: " + json.dumps(item) + "\n\n" for item in chunks)
                        + "data: [DONE]\n\n"
                    ).encode()
                    self.reply_bytes(body, "text/event-stream")
                else:
                    self.reply(
                        {
                            "id": "fixture",
                            "object": "chat.completion",
                            "created": 1,
                            "model": payload["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {
                                        "role": "assistant",
                                        "content": "NEURODESK_OLLAMA_OK",
                                    },
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 100,
                                "completion_tokens": 8,
                                "total_tokens": 108,
                            },
                        }
                    )
            else:
                self.send_error(404)

        def reply(self, payload):
            self.reply_bytes(json.dumps(payload).encode(), "application/json")

        def reply_bytes(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def complete_through_t3(rpc, home, model, expected, *, timeout=60):
    catalog = rpc.call(
        "server.refreshProviders", {"instanceId": "opencode", "refreshModels": True}
    )
    provider = next(
        item for item in catalog["providers"] if item["instanceId"] == "opencode"
    )
    deadline = time.monotonic() + 60
    while not provider["installed"] and time.monotonic() < deadline:
        time.sleep(0.2)
        config = rpc.call("server.getConfig", {})
        provider = next(
            item for item in config["providers"] if item["instanceId"] == "opencode"
        )
    assert provider["installed"] is True, provider
    assert provider["status"] != "error", provider
    slugs = [item["slug"] for item in provider["models"]]
    assert model in slugs, provider
    assert "ollama/devstral-16k:latest" not in slugs, slugs
    project_id, thread_id = str(uuid.uuid4()), str(uuid.uuid4())
    rpc.call(
        "projects.mutate",
        {
            "type": "project.create",
            "commandId": str(uuid.uuid4()),
            "projectId": project_id,
            "title": "Ollama acceptance",
            "workspaceRoot": str(home),
        },
    )
    rpc.call(
        "orchestration.launchThread",
        {
            "commandId": str(uuid.uuid4()),
            "threadId": thread_id,
            "projectId": project_id,
            "title": "Ollama completion",
            "generateTitle": False,
            "modelSelection": {"instanceId": "opencode", "model": model},
            "runtimeMode": "full-access",
            "interactionMode": "default",
            "workspaceStrategy": {"type": "root"},
            "initialMessage": {
                "text": f"Reply with exactly {expected}. Do not use tools.\n/no_think",
                "attachments": [],
            },
        },
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        projection = rpc.call(
            "orchestration.getThreadProjection", {"threadId": thread_id}
        )
        serialized = json.dumps(projection)
        (home / "last-projection.json").write_text(serialized)
        assistant = [
            item["text"]
            for item in projection["turnItems"]
            if item["type"] == "assistant_message"
        ]
        if (
            any(expected in text for text in assistant)
            and projection["runs"][-1]["status"] == "completed"
        ):
            return projection
        if '"status": "failed"' in serialized:
            pytest.fail(serialized[-6000:])
        time.sleep(0.2)
    pytest.fail("T3 did not finish the Ollama turn: " + serialized[-6000:])


def test_t3_first_use_discovers_and_completes_without_interactive_warmup(tmp_path):
    started, release = threading.Event(), threading.Event()
    home = tmp_path / "home"
    with fake_ollama(started, release) as (host, calls), running_t3(home, host) as rpc:
        with ThreadPoolExecutor(max_workers=1) as worker:
            completion = worker.submit(
                complete_through_t3,
                rpc,
                home,
                "ollama/neurodesk-controlled:latest",
                "NEURODESK_OLLAMA_OK",
            )
            try:
                assert started.wait(timeout=50), (
                    "T3 never reached the completion endpoint"
                )
                environment = {**isolated_environment(home), "OLLAMA_HOST": host}
                with running_opencode(home, home, environment) as config:
                    assert (
                        "neurodesk-controlled:latest"
                        in config["provider"]["ollama"]["models"]
                    )
            finally:
                release.set()
            projection = completion.result(timeout=60)
    completions = [
        (headers, body)
        for path, headers, body in calls
        if path == "/v1/chat/completions"
    ]
    assert completions, calls
    assert all("private" not in json.dumps(headers) for _, headers, _ in calls)
    assert len(projection["attempts"]) == 1
    assert "NEURODESK_OLLAMA_OK" in json.dumps(projection)


def test_t3_completes_with_an_actual_ollama_server_at_the_docker_default(tmp_path):
    model = os.environ.get("NEURODESKTOP_TEST_OLLAMA_MODEL")
    if not model:
        pytest.skip("Set NEURODESKTOP_TEST_OLLAMA_MODEL for the live Ollama gate")
    host = "http://host.docker.internal:11434"
    with running_t3(
        tmp_path / "home", host, inline={"agent": {"title": {"disable": True}}}
    ) as rpc:
        complete_through_t3(
            rpc, tmp_path / "home", "ollama/" + model, "OLLAMA_OK", timeout=600
        )


@contextmanager
def running_opencode(home, project, environment):
    port = free_port()
    log = home / "opencode.log"
    with log.open("w") as stream:
        process = subprocess.Popen(
            [LAUNCHER, "serve", "--hostname=127.0.0.1", f"--port={port}"],
            cwd=project,
            env=environment,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            assert process.poll() is None, log.read_text()[-2000:]
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/config", timeout=0.5
                ) as response:
                    config = json.load(response)
                assert "opencode server listening on" in log.read_text()
                yield config
                return
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        pytest.fail("OpenCode did not announce and serve its resolved configuration")
    finally:
        stop_process(process)


def test_installed_opencode_merge_precedence_and_quiet_overlay(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    global_config = home / ".config/opencode/opencode.json"
    global_config.parent.mkdir(parents=True)
    defaults = json.loads(DEFAULTS.read_text())
    defaults["provider"]["ollama"]["models"]["user-saved"] = {
        "name": "User saved",
        "limit": {"context": 4096, "output": 512},
    }
    defaults["instructions"] = ["global-rules.md"]
    global_config.write_text(json.dumps(defaults))
    before = global_config.read_bytes()
    explicit = tmp_path / "explicit.json"
    explicit.write_text(
        json.dumps(
            {"$schema": "https://opencode.ai/config.json", "model": "ollama/from-file"}
        )
    )
    project_config = project / "opencode.jsonc"
    project_config.write_text("""{
      "$schema": "https://opencode.ai/config.json",
      // The native JSONC parser owns these project choices.
      "model": "ollama/user-saved",
      "instructions": ["project-rules.md"],
      "mcp": {"user-disabled": {"type": "local", "command": ["false"], "enabled": false}},
      "provider": {"ollama": {"models": {"project-saved": {"name": "Project saved"}}}}
    }""")
    environment = isolated_environment(home)
    environment["OPENCODE_CONFIG"] = str(explicit)
    with fake_ollama() as (host, _):
        environment["OLLAMA_HOST"] = host
        with running_opencode(home, project, environment) as config:
            assert config["model"] == "ollama/user-saved"
            assert config["instructions"] == ["global-rules.md", "project-rules.md"]
            provider = config["provider"]["ollama"]
            assert provider["options"]["baseURL"] == host + "/v1"
            assert provider["models"]["user-saved"]["limit"] == {
                "context": 4096,
                "output": 512,
            }
            assert provider["whitelist"] == [
                "neurodesk-controlled:latest",
                "user-saved",
                "project-saved",
            ]
            assert config["mcp"]["user-disabled"]["enabled"] is False
        project_config.write_text(
            project_config.read_text().replace(
                '"models": {"project-saved"',
                '"options": {"baseURL": "http://project.example/v1"}, "models": {"project-saved"',
            )
        )
        with running_opencode(home, project, environment) as config:
            assert (
                config["provider"]["ollama"]["options"]["baseURL"]
                == "http://project.example/v1"
            )
            assert "whitelist" not in config["provider"]["ollama"]
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {
                "model": "ollama/from-inline",
                "provider": {
                    "ollama": {
                        "options": {"baseURL": "http://inline.example/v1"},
                        "models": {"inline-saved": {"name": "Inline saved"}},
                    }
                },
            }
        )
        with running_opencode(home, project, environment) as config:
            assert config["model"] == "ollama/from-inline"
            provider = config["provider"]["ollama"]
            assert provider["options"]["baseURL"] == "http://inline.example/v1"
            assert set(provider["models"]) == {
                "devstral-16k:latest",
                "user-saved",
                "project-saved",
                "inline-saved",
            }
    environment.pop("OPENCODE_CONFIG_CONTENT")
    environment["OLLAMA_HOST"] = "http://127.0.0.1:1"
    with running_opencode(home, project, environment) as config:
        assert config["model"] == "ollama/user-saved"
        assert "whitelist" not in config["provider"]["ollama"]
        assert "user-saved" in config["provider"]["ollama"]["models"]
    assert global_config.read_bytes() == before


def test_quiet_acp_initialization_keeps_stdout_as_json(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    environment = isolated_environment(home)
    with fake_ollama() as (host, _):
        environment["OLLAMA_HOST"] = host
        process = subprocess.Popen(
            [LAUNCHER, "acp"],
            env=environment,
            cwd=home,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        try:
            request = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": 1, "clientCapabilities": {}},
                    }
                )
                + "\n"
            )
            output, _ = process.communicate(request, timeout=40)
            messages = [json.loads(line) for line in output.splitlines()]
            result = next(message for message in messages if message.get("id") == 1)
            assert "error" not in result, result
            assert result["result"]["protocolVersion"] == 1
        finally:
            stop_process(process)
