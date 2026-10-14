#!/usr/bin/env python3
"""Mirror the model selected in OpenCode into Notebook Intelligence (NBI).

The OpenCode wrapper stores the chosen model as ``provider/model_id`` in
``~/.config/opencode/opencode.json`` together with each provider's baseURL, so
that file is the single source of truth for the coding agents' model and
endpoint. ``nbi_setup.sh`` calls this helper to point NBI's openai-compatible
``chat_model`` and ``inline_completion_model`` sections at the same model.

Sections using another provider (claude, ollama, ...) and sections the user
pointed at an endpoint OpenCode does not manage are left alone. The
llm.neurodesk.org key reaches NBI only for that exact HTTPS authority.

Usage::

  NEURODESK_API_KEY_VALUE=... nbi_mirror_opencode.py NBI_CONFIG OPENCODE_CONFIG

Credentials arrive through the environment, never argv: the Neurodesk key in
``NEURODESK_API_KEY_VALUE`` and any other ``{env:VAR}`` placeholder in
OpenCode's provider options from ``VAR``. Nothing is written to stdout. When
either config is missing, corrupt or names no usable openai-compatible model,
the helper exits 0 without writing. When NBI changes, the config is replaced
atomically with a 0600 file and one line naming the selection goes to stderr.
If the write fails, the existing file is left untouched, one diagnostic line
goes to stderr and the exit status is 1.
"""

import argparse
from collections.abc import Mapping
import contextlib
import copy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

from provider_security import is_neurodesk_endpoint

JsonObject = dict[str, Any]

NBI_SECTIONS = ("chat_model", "inline_completion_model")
OPENAI_COMPATIBLE_NPM = "@ai-sdk/openai-compatible"
NBI_OPENAI_COMPATIBLE = "openai-compatible"
NEURODESK_KEY_VARIABLE = "NEURODESK_API_KEY"
# Always managed, even if a user's opencode.json no longer lists it.
NEURODESK_BASE_URL = "https://llm.neurodesk.org/openai"
DIAGNOSTIC_PREFIX = "nbi_setup.sh"


class ConfigWriteError(Exception):
    """The NBI config could not be replaced; the message is safe to print."""


@dataclass(frozen=True)
class Selection:
    """What NBI should use, resolved from opencode.json."""

    provider_key: str
    model_id: str
    base_url: str
    api_key: str
    context_window: str
    managed_urls: frozenset[str]

    @property
    def label(self) -> str:
        return f"{self.provider_key}/{self.model_id}"


def _base_url(options: object) -> str:
    if not isinstance(options, dict):
        return ""
    return str(options.get("baseURL") or "").rstrip("/")


def _context_window(provider_cfg: JsonObject, model_id: str) -> str:
    """The model's positive context limit as a decimal string, or ``""``."""
    models = provider_cfg.get("models")
    model_cfg = models.get(model_id) if isinstance(models, dict) else None
    limit = model_cfg.get("limit") if isinstance(model_cfg, dict) else None
    raw = limit.get("context") if isinstance(limit, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return ""
    if isinstance(raw, float) and not math.isfinite(raw):
        return ""
    try:
        value = int(raw)
    except ValueError:
        return ""
    return str(value) if value > 0 else ""


def _resolve_api_key(
    raw: object, base_url: str, neurodesk_key: str, environ: Mapping[str, str]
) -> str:
    """Resolve OpenCode's ``{env:VAR}`` placeholders to the literal NBI needs."""
    text = str(raw or "")
    if not (text.startswith("{env:") and text.endswith("}")):
        return text
    variable = text[5:-1]
    if variable == NEURODESK_KEY_VARIABLE:
        return neurodesk_key if is_neurodesk_endpoint(base_url) else ""
    return environ.get(variable, "")


def read_selection(
    opencode_cfg: object, neurodesk_key: str, environ: Mapping[str, str]
) -> Selection | None:
    """The openai-compatible model OpenCode selected, or None if there is none."""
    if not isinstance(opencode_cfg, dict):
        return None
    model_ref = opencode_cfg.get("model")
    if not isinstance(model_ref, str) or "/" not in model_ref:
        return None
    provider_key, model_id = model_ref.split("/", 1)
    providers = opencode_cfg.get("provider")
    if not isinstance(providers, dict):
        return None
    provider_cfg = providers.get(provider_key)
    if not isinstance(provider_cfg, dict):
        return None
    if provider_cfg.get("npm") != OPENAI_COMPATIBLE_NPM:
        return None
    options = provider_cfg.get("options")
    base_url = _base_url(options)
    if not base_url or not model_id:
        return None
    raw_key = options.get("apiKey") if isinstance(options, dict) else None
    # Every baseURL OpenCode knows about counts as managed; an NBI section
    # pointing anywhere else was configured by hand.
    managed_urls = {NEURODESK_BASE_URL}
    for candidate in providers.values():
        if isinstance(candidate, dict):
            url = _base_url(candidate.get("options"))
            if url:
                managed_urls.add(url)
    return Selection(
        provider_key=provider_key,
        model_id=model_id,
        base_url=base_url,
        api_key=_resolve_api_key(raw_key, base_url, neurodesk_key, environ),
        context_window=_context_window(provider_cfg, model_id),
        managed_urls=frozenset(managed_urls),
    )


def _get_prop(props: list[Any], prop_id: str) -> str:
    for prop in props:
        if isinstance(prop, dict) and prop.get("id") == prop_id:
            return str(prop.get("value") or "")
    return ""


def _set_prop(props: list[Any], prop_id: str, value: str) -> bool:
    """Set the first ``prop_id`` property, appending one if absent."""
    for prop in props:
        if isinstance(prop, dict) and prop.get("id") == prop_id:
            if prop.get("value") == value:
                return False
            prop["value"] = value
            return True
    props.append({"id": prop_id, "value": value})
    return True


def apply_selection(nbi_cfg: JsonObject, selection: Selection) -> JsonObject | None:
    """A copy of ``nbi_cfg`` following ``selection``, or None if unchanged."""
    cfg = copy.deepcopy(nbi_cfg)
    changed = False
    for section_name in NBI_SECTIONS:
        section = cfg.get(section_name)
        if not isinstance(section, dict):
            continue
        if section.get("provider") != NBI_OPENAI_COMPATIBLE:
            continue
        props = section.get("properties")
        if not isinstance(props, list):
            props = []
            section["properties"] = props
        current_url = _get_prop(props, "base_url").rstrip("/")
        if current_url and current_url not in selection.managed_urls:
            continue
        # Evaluate every update; do not short-circuit after the first change.
        changed = _set_prop(props, "base_url", selection.base_url) or changed
        changed = _set_prop(props, "model_id", selection.model_id) or changed
        # Keep a missing key only while staying on the same endpoint.
        if selection.api_key or current_url != selection.base_url:
            changed = _set_prop(props, "api_key", selection.api_key) or changed
        if selection.context_window:
            changed = (
                _set_prop(props, "context_window", selection.context_window) or changed
            )
    return cfg if changed else None


def load_json(path: Path) -> object:
    """Parsed JSON from ``path``, or None when it is unreadable or invalid."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, RecursionError):
        return None


def write_json_atomic(path: Path, data: JsonObject) -> None:
    """Replace ``path`` with ``data`` as a 0600 file in one rename."""
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        tmp_path.unlink(missing_ok=True)
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    except OSError as exc:
        # Best effort: a failed cleanup must not mask the original error.
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise ConfigWriteError(f"cannot write {path}: {exc.strerror or exc}") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mirror the OpenCode model selection into NBI's config."
    )
    parser.add_argument("nbi_config", type=Path, help="NBI config.json to update")
    parser.add_argument("opencode_config", type=Path, help="user opencode.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    selection = read_selection(
        load_json(args.opencode_config),
        os.environ.get("NEURODESK_API_KEY_VALUE", ""),
        os.environ,
    )
    nbi_cfg = load_json(args.nbi_config)
    if selection is None or not isinstance(nbi_cfg, dict):
        return 0
    updated = apply_selection(nbi_cfg, selection)
    if updated is None:
        return 0
    try:
        write_json_atomic(args.nbi_config, updated)
    except ConfigWriteError as exc:
        print(f"{DIAGNOSTIC_PREFIX}: {exc}", file=sys.stderr)
        return 1
    print(
        f"{DIAGNOSTIC_PREFIX}: synced Notebook Intelligence to OpenCode selection "
        f"{selection.label}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
