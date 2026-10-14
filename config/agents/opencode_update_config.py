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
stderr and the exit status is 1. Nothing is written to stdout.

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
import sys
from typing import Any

JsonObject = dict[str, Any]

MANAGED_PROVIDERS = ("jetstream", "ollama", "neurodesk")
LEGACY_GLOBAL_INSTRUCTION = "/opt/AGENTS.md"
NEURODESK_PROVIDER_NAME = "Neurodesk LLMs"
NEURODESK_API_KEY_REF = "{env:NEURODESK_API_KEY}"
FALLBACK_OLLAMA_CONTEXT_LIMIT = 16384
FALLBACK_OLLAMA_OUTPUT_LIMIT = 8192


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
    Otherwise a still-valid current default is kept, and an invalid one is
    replaced with the first model of the first managed provider that has any.
    """
    cfg = copy.deepcopy(cfg)
    drop_legacy_global_instruction(cfg)

    providers = _object(cfg.get("provider"))
    default_providers = _object(default_cfg.get("provider"))
    for provider_key in MANAGED_PROVIDERS:
        _refresh_provider(providers, default_providers, provider_key, models)
    cfg["provider"] = providers

    if selected_model and _model_exists(providers, selected_model):
        cfg["model"] = selected_model
    elif not _model_exists(providers, cfg.get("model")):
        for provider_key in MANAGED_PROVIDERS:
            models_cfg = providers[provider_key]["models"]
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
        models={key: getattr(args, f"{key}_models") for key in MANAGED_PROVIDERS},
        base_urls=base_urls,
        ollama_contexts=parse_ollama_contexts(args.ollama_contexts),
        ollama_default_context=positive_int(args.ollama_default_context)
        or FALLBACK_OLLAMA_CONTEXT_LIMIT,
        ollama_default_output=positive_int(args.ollama_default_output)
        or FALLBACK_OLLAMA_OUTPUT_LIMIT,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
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
