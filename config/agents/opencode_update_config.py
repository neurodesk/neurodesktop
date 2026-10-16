#!/usr/bin/env python3
"""Refresh the managed model providers in a user's OpenCode config.

The OpenCode wrapper probes Jetstream, a local Ollama server and
llm.neurodesk.org, then calls this helper to record the detected models in
``~/.config/opencode/opencode.json`` and, optionally, select a default model.

Only the ``jetstream``, ``ollama`` and ``neurodesk`` providers are managed.
Other providers, MCP servers, user instructions, per-model customisations and
extra provider options are preserved. Models a provider no longer lists are
dropped. The config is replaced atomically and keeps its file mode; on any
error the existing file is left untouched, a one-line diagnostic is written to
stderr and the exit status is 1. Config updates write nothing to stdout.

``--discover-ollama`` emits one successful catalog as JSON for the interactive
wrapper. ``--quiet-exec BINARY DEFAULTS ARGS...`` supplies a process-local
Ollama overlay before exec, without prompts or config writes.

Usage::

  opencode_update_config.py CONFIG DEFAULTS [--select=PROVIDER/MODEL]
      [--jetstream-model=ID]... [--ollama-model=ID]... [--neurodesk-model=ID]...
      [--ollama-contexts=JSON] [--ollama-default-context=N]
      [--ollama-default-output=N] [--ollama-base-url=URL]
      [--jetstream-base-url=URL] [--neurodesk-base-url=URL]

``DEFAULTS`` is the image's default config; it seeds providers and models the
user config lacks and may be absent.
"""

import argparse
import copy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

JsonObject = dict[str, Any]

MANAGED_PROVIDERS = ("jetstream", "ollama", "neurodesk")
LEGACY_GLOBAL_INSTRUCTION = "/opt/AGENTS.md"
NEURODESK_PROVIDER_NAME = "Neurodesk LLMs"
NEURODESK_API_KEY_REF = "{env:NEURODESK_API_KEY}"
FALLBACK_OLLAMA_CONTEXT_LIMIT = 16384
FALLBACK_OLLAMA_OUTPUT_LIMIT = 8192
DEFAULT_OLLAMA_HOST = "http://host.docker.internal:11434"
OLLAMA_DISCOVERY_SECONDS = 6.0
OLLAMA_DISCOVERY_CONTEXT_LIMIT = 32768


@dataclass(frozen=True)
class OllamaSnapshot:
    """A successful listing, including an intentionally empty catalog."""

    base_url: str
    contexts: dict[str, int]


def ollama_base_url(host: str) -> str | None:
    """Normalize an unauthenticated Ollama endpoint to its OpenAI base URL."""
    try:
        parsed = urlsplit(host)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        _ = parsed.port
    except ValueError:
        return None
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _ollama_json(url: str, deadline: float, body: JsonObject | None = None) -> Any:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    # curl bounds DNS, transfer time and response size. Never follow redirects
    # or attach the credentials exported for unrelated providers.
    with tempfile.TemporaryFile() as response:
        args = [
            "curl",
            "--disable",
            "--silent",
            "--fail",
            "--max-time",
            str(remaining),
            "--max-filesize",
            "2097152",
            "--proto",
            "=http,https",
        ]
        if body is not None:
            args += [
                "--header",
                "Content-Type: application/json",
                "--data-binary",
                json.dumps(body),
            ]
        args += [url]
        try:
            result = subprocess.run(
                args,
                stdout=response,
                stderr=subprocess.DEVNULL,
                timeout=remaining,
                check=False,
            )
            if result.returncode:
                return None
            response.seek(0)
            return json.loads(response.read(2097153))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None


def ollama_context(payload: object) -> int | None:
    """Read native show metadata and explicit num_ctx model parameters."""
    data = _object(payload)
    fields = ("context_length", "num_ctx", "max_context_length", "max_sequence_length")
    # A model's explicit num_ctx overrides its architecture's trained window.
    for key in ("parameters", "modelfile"):
        raw = data.get(key)
        if isinstance(raw, str):
            match = re.search(
                r"(?im)^\s*(?:parameter\s+)?(?:num_ctx|context_length)\s+(\d+)\s*$", raw
            )
            if match and (value := positive_int(match[1])):
                return value
    for section in (
        data,
        _object(data.get("details")),
        _object(data.get("model_info")),
    ):
        for key, raw_value in section.items():
            if key in fields or key.endswith(".context_length"):
                if value := positive_int(raw_value):
                    return value
    return None


def discover_ollama(
    host: str, *, deadline_seconds: float = OLLAMA_DISCOVERY_SECONDS
) -> OllamaSnapshot | None:
    """Bound the entire tags/show observation. None preserves saved state."""
    base_url = ollama_base_url(host)
    if base_url is None:
        return None
    deadline = time.monotonic() + deadline_seconds
    payload = _ollama_json(base_url.removesuffix("/v1") + "/api/tags", deadline)
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return None
    names: dict[str, None] = {}
    for model in payload["models"]:
        name = _object(model).get("name")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 512
            or any(ord(c) < 32 for c in name)
        ):
            return None
        names[name] = None
    if len(names) > 256:
        return None
    contexts = {}
    for name in names:
        show = _ollama_json(
            base_url.removesuffix("/v1") + "/api/show", deadline, {"name": name}
        )
        contexts[name] = ollama_context(show) or OLLAMA_DISCOVERY_CONTEXT_LIMIT
    return OllamaSnapshot(base_url, contexts)


def ollama_is_managed(provider: JsonObject, base_url: str) -> bool:
    options = _object(provider.get("options"))
    managed_endpoints = {
        ollama_base_url(DEFAULT_OLLAMA_HOST),
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        base_url,
    }
    if provider.get(
        "npm", "@ai-sdk/openai-compatible"
    ) != "@ai-sdk/openai-compatible" or (
        options.get("baseURL") and options["baseURL"] not in managed_endpoints
    ):
        return False
    return True


def quiet_ollama_overlay(
    snapshot: OllamaSnapshot, existing: JsonObject, defaults: JsonObject
) -> JsonObject:
    """Supply only missing Ollama data, keeping caller configuration in place."""
    provider = _object(_object(existing.get("provider")).get("ollama"))
    if not ollama_is_managed(provider, snapshot.base_url):
        return {}
    saved = _object(provider.get("models"))
    seeds = _object(
        _object(_object(defaults.get("provider")).get("ollama")).get("models")
    )
    models: JsonObject = {}
    for name, context in snapshot.contexts.items():
        current = _object(saved.get(name))
        limits = _object(current.get("limit"))
        if current == seeds.get(name):
            limits = {}
        context = positive_int(limits.get("context")) or context
        output = min(
            positive_int(limits.get("output")) or FALLBACK_OLLAMA_OUTPUT_LIMIT, context
        )
        models[name] = {"limit": {"context": context, "output": output}}
        if "name" not in current:
            models[name]["name"] = name
    delta: JsonObject = {"models": models}
    if not provider.get("npm"):
        delta["npm"] = "@ai-sdk/openai-compatible"
    if not provider.get("name"):
        delta["name"] = "Local Ollama"
    delta["options"] = {"baseURL": snapshot.base_url}
    if "whitelist" not in provider:
        # OpenCode deep-merges model maps. A whitelist hides the image's stale
        # seed while retaining user models and explicit default selections.
        visible = dict.fromkeys(snapshot.contexts)
        visible.update(
            {name: None for name, model in saved.items() if model != seeds.get(name)}
        )
        for key in ("model", "small_model"):
            selected = existing.get(key)
            if isinstance(selected, str) and selected.startswith("ollama/"):
                visible[selected.removeprefix("ollama/")] = None
        delta["whitelist"] = list(visible)
    return {"provider": {"ollama": delta}}


class ConfigError(Exception):
    """A config file could not be read, parsed or written."""


DEFAULT_BASE_URLS = {
    "jetstream": "https://llm.jetstream-cloud.org/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "neurodesk": "https://llm.neurodesk.org/openai",
}


@dataclass
class DetectedModels:
    """Models and endpoints the wrapper detected for the managed providers."""

    models: dict[str, list[str]] = field(default_factory=dict)
    base_urls: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BASE_URLS))
    ollama_contexts: JsonObject = field(default_factory=dict)
    ollama_default_context: int = FALLBACK_OLLAMA_CONTEXT_LIMIT
    ollama_default_output: int = FALLBACK_OLLAMA_OUTPUT_LIMIT


def positive_int(value: Any) -> int | None:
    """Return ``value`` as a positive integer, or None if it is not one."""
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def parse_ollama_contexts(raw: str) -> JsonObject:
    """Parse the wrapper's probed ``{model: context}`` map.

    Context probing is best effort, so empty or malformed input yields no
    probed contexts and the default limit applies.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return _object(parsed)


def builtin_provider(provider_key: str, models: DetectedModels) -> JsonObject:
    """Return the provider definition used when no config supplies one."""
    names = {
        "neurodesk": NEURODESK_PROVIDER_NAME,
        "jetstream": "JetStream",
        "ollama": "Local Ollama",
    }
    options: JsonObject = {"baseURL": models.base_urls[provider_key]}
    if provider_key == "neurodesk":
        options["apiKey"] = NEURODESK_API_KEY_REF
    return {
        "npm": "@ai-sdk/openai-compatible",
        "name": names[provider_key],
        "options": options,
        "models": {},
    }


def _object(value: object) -> JsonObject:
    return value if isinstance(value, dict) else {}


def drop_legacy_global_instruction(cfg: JsonObject) -> None:
    """Remove the read-only /opt/AGENTS.md entry earlier releases pinned.

    The wrapper copies an editable AGENTS.md into the working directory; the
    legacy entry put a second, uneditable copy into every prompt.
    """
    instructions = cfg.get("instructions")
    if not isinstance(instructions, list):
        return
    kept = [
        entry
        for entry in instructions
        if not (isinstance(entry, str) and entry.strip() == LEGACY_GLOBAL_INSTRUCTION)
    ]
    if kept:
        cfg["instructions"] = kept
    else:
        cfg.pop("instructions", None)


def _ollama_limits(
    model_cfg: JsonObject, model_id: str, models: DetectedModels
) -> JsonObject:
    limit_cfg = _object(model_cfg.get("limit"))
    context_limit = (
        positive_int(limit_cfg.get("context"))
        or positive_int(models.ollama_contexts.get(model_id))
        or models.ollama_default_context
    )
    output_limit = positive_int(limit_cfg.get("output")) or models.ollama_default_output
    limit_cfg["context"] = context_limit
    limit_cfg["output"] = min(output_limit, context_limit)
    return limit_cfg


def _refresh_provider(
    providers: JsonObject,
    default_providers: JsonObject,
    provider_key: str,
    models: DetectedModels,
) -> None:
    provider_cfg = providers.get(provider_key)
    if not isinstance(provider_cfg, dict):
        default_provider = default_providers.get(provider_key)
        if isinstance(default_provider, dict):
            provider_cfg = copy.deepcopy(default_provider)
        else:
            provider_cfg = builtin_provider(provider_key, models)
        providers[provider_key] = provider_cfg

    if provider_key == "ollama" and not ollama_is_managed(
        provider_cfg, models.base_urls["ollama"]
    ):
        return

    options = provider_cfg.get("options")
    if not isinstance(options, dict):
        options = {}
        provider_cfg["options"] = options
    options["baseURL"] = models.base_urls[provider_key]
    if provider_key == "neurodesk":
        provider_cfg["name"] = NEURODESK_PROVIDER_NAME
        options.setdefault("apiKey", NEURODESK_API_KEY_REF)

    existing_models = _object(provider_cfg.get("models"))
    default_models = _object(_object(default_providers.get(provider_key)).get("models"))

    updated_models: JsonObject = {}
    for model_id in models.models.get(provider_key, []):
        if not isinstance(model_id, str) or not model_id:
            continue
        source = existing_models.get(model_id)
        if not isinstance(source, dict):
            source = default_models.get(model_id)
        model_cfg: JsonObject = (
            copy.deepcopy(source) if isinstance(source, dict) else {"name": model_id}
        )
        model_cfg.setdefault("name", model_id)
        if provider_key == "ollama":
            model_cfg["limit"] = _ollama_limits(model_cfg, model_id, models)
        updated_models[model_id] = model_cfg

    provider_cfg["models"] = updated_models


def _model_exists(providers: JsonObject, model_ref: object) -> bool:
    if not isinstance(model_ref, str) or "/" not in model_ref:
        return False
    provider_key, model_id = model_ref.split("/", 1)
    models_cfg = _object(providers.get(provider_key)).get("models")
    return isinstance(models_cfg, dict) and model_id in models_cfg


def update_config(
    cfg: JsonObject,
    default_cfg: JsonObject,
    models: DetectedModels,
    selected_model: str = "",
) -> JsonObject:
    """Return ``cfg`` with refreshed managed providers and default model.

    ``selected_model`` becomes the default when it names a known model.
    Otherwise an explicit current default is kept, including offline models.
    A missing default uses the first observed managed provider with models.
    """
    cfg = copy.deepcopy(cfg)
    drop_legacy_global_instruction(cfg)

    providers = _object(cfg.get("provider"))
    default_providers = _object(default_cfg.get("provider"))
    for provider_key in models.models:
        _refresh_provider(providers, default_providers, provider_key, models)
    cfg["provider"] = providers

    if selected_model and _model_exists(providers, selected_model):
        cfg["model"] = selected_model
    elif not isinstance(cfg.get("model"), str) or not cfg["model"]:
        for provider_key in MANAGED_PROVIDERS:
            models_cfg = _object(_object(providers.get(provider_key)).get("models"))
            if models_cfg:
                cfg["model"] = f"{provider_key}/{next(iter(models_cfg))}"
                break
    return cfg


def load_json_object(path: Path) -> JsonObject:
    """Read a JSON object from ``path``; raise ConfigError otherwise."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not contain a JSON object")
    return data


def write_json_atomic(path: Path, data: JsonObject) -> None:
    """Replace ``path`` with ``data`` in one rename, keeping its file mode."""
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        if path.exists():
            os.chmod(tmp_path, path.stat().st_mode & 0o7777)
        os.replace(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ConfigError(f"cannot write {path}: {exc.strerror or exc}") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh managed model providers in an OpenCode config."
    )
    parser.add_argument("config", type=Path, help="user opencode.json to update")
    parser.add_argument("defaults", type=Path, help="image default opencode.json")
    parser.add_argument("--select", default="", metavar="PROVIDER/MODEL")
    parser.add_argument(
        "--observed-provider", action="append", choices=MANAGED_PROVIDERS, default=[]
    )
    for provider_key in MANAGED_PROVIDERS:
        parser.add_argument(
            f"--{provider_key}-model",
            dest=f"{provider_key}_models",
            action="append",
            default=[],
            metavar="ID",
        )
        parser.add_argument(f"--{provider_key}-base-url", metavar="URL")
    parser.add_argument("--ollama-contexts", default="", metavar="JSON")
    parser.add_argument("--ollama-default-context", default="", metavar="N")
    parser.add_argument("--ollama-default-output", default="", metavar="N")
    return parser.parse_args(argv)


def detected_models_from_args(args: argparse.Namespace) -> DetectedModels:
    base_urls = dict(DEFAULT_BASE_URLS)
    for provider_key in MANAGED_PROVIDERS:
        base_url = getattr(args, f"{provider_key}_base_url")
        if base_url:
            base_urls[provider_key] = base_url
    return DetectedModels(
        models={
            key: getattr(args, f"{key}_models")
            for key in MANAGED_PROVIDERS
            if getattr(args, f"{key}_models") or key in args.observed_provider
        },
        base_urls=base_urls,
        ollama_contexts=parse_ollama_contexts(args.ollama_contexts),
        ollama_default_context=positive_int(args.ollama_default_context)
        or FALLBACK_OLLAMA_CONTEXT_LIMIT,
        ollama_default_output=positive_int(args.ollama_default_output)
        or FALLBACK_OLLAMA_OUTPUT_LIMIT,
    )


def exec_quiet_opencode(binary: str, defaults_path: Path, argv: list[str]) -> None:
    if argv and argv[0] in ("serve", "acp"):
        try:
            inline = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
            if not isinstance(inline, dict):
                raise ValueError("inline config is not an object")
            # Ask the installed parser about JSONC, environment substitution,
            # project files and custom config paths. Never re-emit its redacted
            # secrets or copy its resolved top-level values into inline config.
            probe = subprocess.run(
                [binary, "debug", "config", "--pure"],
                env={
                    **os.environ,
                    "OPENCODE_CONFIG_CONTENT": os.environ.get("OPENCODE_CONFIG_CONTENT")
                    or "{}",
                },
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            existing = json.loads(probe.stdout)
            if not isinstance(existing, dict):
                raise ValueError("resolved config is not an object")
            snapshot = discover_ollama(
                os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST
            )
            if snapshot is not None:
                defaults = (
                    load_json_object(defaults_path) if defaults_path.exists() else {}
                )
                overlay = quiet_ollama_overlay(snapshot, existing, defaults)
                if overlay:
                    providers = inline.setdefault("provider", {})
                    if "ollama" not in providers:
                        providers["ollama"] = overlay["provider"]["ollama"]
                        content = json.dumps(inline)
                        if len(content.encode()) <= 65536:
                            os.environ["OPENCODE_CONFIG_CONTENT"] = content
        except (
            ConfigError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
            TypeError,
        ):
            pass
    os.execv(binary, [binary, *argv])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--quiet-exec":
        exec_quiet_opencode(argv[1], Path(argv[2]), argv[3:])
        return 0
    if argv == ["--discover-ollama"]:
        snapshot = discover_ollama(os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST)
        if snapshot is None:
            return 1
        print(
            json.dumps({"base_url": snapshot.base_url, "contexts": snapshot.contexts})
        )
        return 0
    args = parse_args(argv)
    try:
        cfg = load_json_object(args.config)
        default_cfg = load_json_object(args.defaults) if args.defaults.exists() else {}
        updated = update_config(
            cfg, default_cfg, detected_models_from_args(args), args.select
        )
        write_json_atomic(args.config, updated)
    except ConfigError as exc:
        print(f"opencode_update_config: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
