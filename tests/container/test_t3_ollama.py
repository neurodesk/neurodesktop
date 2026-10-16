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
import urllib.parse
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
    plugin = home / ".config/opencode/plugins/ollama-config.js"
    if not plugin.exists():
        plugin.parent.mkdir(parents=True, exist_ok=True)
        source = resolve_source(
            "/opt/jovyan_defaults/.config/opencode/plugins/ollama-config.js",
            "config/agents/opencode_ollama_config.js",
        )
        helper = resolve_source(
            "/opt/neurodesktop/opencode_update_config.py",
            "config/agents/opencode_update_config.py",
        )
        plugin.write_text(
            source.read_text().replace(
                "/opt/neurodesktop/opencode_update_config.py", str(helper)
            )
        )
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
def fake_ollama(completion_started=None, completion_release=None, *, names=None):
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply(
                {
                    "models": [
                        {"name": name}
                        for name in (
                            ["neurodesk-controlled:latest"] if names is None else names
                        )
                    ]
                }
            )

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
                "text": f"Reply with exactly {expected or 'HELLO'}. Do not use tools.\n/no_think",
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
            any(text.strip() for text in assistant)
            and (expected is None or any(expected in text for text in assistant))
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
                with running_opencode(home, home, environment) as read_config:
                    config = read_config()
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
        projection = complete_through_t3(
            rpc, tmp_path / "home", "ollama/" + model, None, timeout=600
        )
    assert len(projection["attempts"]) == 1


@contextmanager
def running_opencode(home, project, environment, *, launcher=LAUNCHER):
    port = free_port()
    log = home / f"opencode-{port}.log"
    with log.open("w") as stream:
        process = subprocess.Popen(
            [launcher, "serve", "--hostname=127.0.0.1", f"--port={port}"],
            cwd=project,
            env=environment,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )

    def read_config(directory=None):
        query = (
            ""
            if directory is None
            else "?" + urllib.parse.urlencode({"directory": str(directory)})
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/config{query}", timeout=15
        ) as response:
            return json.load(response)

    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            assert process.poll() is None, log.read_text()[-2000:]
            try:
                read_config()
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        else:
            pytest.fail(
                "OpenCode did not announce and serve its resolved configuration"
            )
        assert "opencode server listening on" in log.read_text()
        yield read_config
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
    global_config.write_text("// User global JSONC.\n" + json.dumps(defaults))
    before = global_config.read_bytes()
    explicit = tmp_path / "explicit.jsonc"
    explicit.write_text(
        "// Explicit config JSONC.\n"
        + json.dumps(
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
        with running_opencode(home, project, environment) as read_config:
            config = read_config()
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
        with running_opencode(home, project, environment) as read_config:
            config = read_config()
            assert (
                config["provider"]["ollama"]["options"]["baseURL"]
                == "http://project.example/v1"
            )
            assert "whitelist" not in config["provider"]["ollama"]
        environment["OPENCODE_CONFIG_CONTENT"] = "// Inline JSONC.\n" + json.dumps(
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
        with running_opencode(home, project, environment) as read_config:
            config = read_config()
            assert config["model"] == "ollama/from-inline"
            provider = config["provider"]["ollama"]
            assert provider["options"]["baseURL"] == "http://inline.example/v1"
            assert set(provider["models"]) == {
                "devstral-16k:latest",
                "user-saved",
                "project-saved",
                "inline-saved",
            }
        environment["USER_MODEL_NAME"] = "Intentional-{env:SECOND_PASS}"
        environment["SECOND_PASS"] = "must-not-expand-twice"
        environment["OPENCODE_CONFIG_CONTENT"] = (
            "// Inline managed provider.\n"
            + json.dumps(
                {
                    "model": "ollama/neurodesk-controlled:latest",
                    "provider": {
                        "ollama": {
                            "options": {"baseURL": host + "/v1"},
                            "models": {
                                "neurodesk-controlled:latest": {
                                    "name": "{env:USER_MODEL_NAME}",
                                    "limit": {"context": 2048, "output": 512},
                                }
                            },
                        }
                    },
                }
            )
        )
        with running_opencode(home, project, environment) as read_config:
            config = read_config()
            assert config["model"] == "ollama/neurodesk-controlled:latest"
            provider = config["provider"]["ollama"]
            assert provider["models"]["neurodesk-controlled:latest"] == {
                "name": "Intentional-{env:SECOND_PASS}",
                "limit": {"context": 2048, "output": 512},
            }
            assert "user-saved" in provider["whitelist"]
    environment.pop("OPENCODE_CONFIG_CONTENT")
    environment["OLLAMA_HOST"] = "http://127.0.0.1:1"
    with running_opencode(home, project, environment) as read_config:
        config = read_config()
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


@pytest.mark.parametrize("custom_endpoint", [True, False])
def test_quiet_server_resolves_later_project_config(tmp_path, custom_endpoint):
    home, project = tmp_path / "home", tmp_path / "later-project"
    home.mkdir()
    global_config = home / ".config/opencode/opencode.json"
    global_config.parent.mkdir(parents=True)
    shutil.copy(DEFAULTS, global_config)
    saved = global_config.read_bytes()
    environment = isolated_environment(home)
    with fake_ollama() as (host, _):
        environment["OLLAMA_HOST"] = host
        with running_opencode(home, home, environment) as read_config:
            project.mkdir()
            provider = {
                "models": {
                    "neurodesk-controlled:latest": {
                        "limit": {"context": 2048, "output": 512}
                    },
                    "project-selected": {"name": "Selected offline model"},
                },
            }
            if custom_endpoint:
                provider["options"] = {"baseURL": "http://project.example/v1"}
            project_config = project / "opencode.jsonc"
            project_config.write_text(
                "// Created after the server started outside this directory.\n"
                + json.dumps(
                    {
                        "$schema": "https://opencode.ai/config.json",
                        "model": "ollama/project-selected",
                        "provider": {"ollama": provider},
                    }
                )
            )
            before = project_config.read_bytes()
            actual = read_config(project)
            with running_opencode(
                home,
                home,
                environment,
                launcher=os.environ.get(
                    "NEURODESKTOP_TEST_OPENCODE_BIN", "/usr/bin/opencode"
                ),
            ) as native_config:
                expected = native_config(project)
            assert actual["model"] == expected["model"] == "ollama/project-selected"
            ollama = actual["provider"]["ollama"]
            if custom_endpoint:
                assert ollama == expected["provider"]["ollama"]
            else:
                assert ollama["models"]["neurodesk-controlled:latest"]["limit"] == {
                    "context": 2048,
                    "output": 512,
                }
                assert "project-selected" in ollama["whitelist"]
                assert (
                    read_config()["provider"]["ollama"]["models"][
                        "neurodesk-controlled:latest"
                    ]["limit"]["context"]
                    == 65536
                )
            assert project_config.read_bytes() == before
    assert global_config.read_bytes() == saved


@pytest.mark.parametrize("reference", ["env", "file"])
def test_discovered_model_cannot_interpolate_private_config(tmp_path, reference):
    home = tmp_path / "home"
    home.mkdir()
    secret = "canary-discovered-model-private-9a831"
    secret_file = home / "private-key"
    secret_file.write_text(secret)
    malicious = "probe-" + (
        "{env:NEURODESK_API_KEY}"
        if reference == "env"
        else "{file:" + str(secret_file) + "}"
    )
    config_path = home / ".config/opencode/opencode.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "ollama": {
                        "npm": "@ai-sdk/openai-compatible",
                        "models": {
                            "user-model": {
                                "name": "{env:USER_MODEL_NAME}",
                                "limit": {"context": 2048, "output": 512},
                            },
                        },
                    }
                },
                "model": "ollama/user-model",
            }
        )
    )
    saved = config_path.read_bytes()
    environment = isolated_environment(home)
    environment.update(
        NEURODESK_API_KEY=secret, USER_MODEL_NAME="Intentional user reference"
    )
    with fake_ollama(names=[malicious]) as (host, _):
        environment["OLLAMA_HOST"] = host
        with running_opencode(home, home, environment) as read_config:
            provider = read_config()["provider"]["ollama"]
            assert secret not in json.dumps(provider)
            assert malicious not in provider["models"]
            assert (
                provider["models"]["user-model"]["name"] == "Intentional user reference"
            )
    assert config_path.read_bytes() == saved


@pytest.mark.parametrize("online", [True, False])
def test_quiet_empty_catalog_and_offline_saved_models(tmp_path, online):
    home = tmp_path / "home"
    home.mkdir()
    path = home / ".config/opencode/opencode.json"
    path.parent.mkdir(parents=True)
    config = json.loads(DEFAULTS.read_text())
    config["model"] = "ollama/user-saved"
    config["provider"]["ollama"]["models"]["user-saved"] = {
        "name": "Saved",
        "limit": {"context": 2048, "output": 512},
    }
    path.write_text(json.dumps(config))
    before = path.read_bytes()
    environment = isolated_environment(home)
    with fake_ollama(names=[]) as (host, _):
        environment["OLLAMA_HOST"] = host if online else "http://127.0.0.1:1"
        with running_opencode(home, home, environment) as read_config:
            actual = read_config()
            assert actual["model"] == "ollama/user-saved"
            ollama = actual["provider"]["ollama"]
            assert (
                ollama["models"]["user-saved"]
                == config["provider"]["ollama"]["models"]["user-saved"]
            )
            if online:
                assert ollama["whitelist"] == ["user-saved"]
            else:
                assert ollama == config["provider"]["ollama"]
    assert path.read_bytes() == before
