#!/usr/bin/env python3
"""Replace GitHub's public revoke-credentials samples in ghapi's API docs.

These five published examples trigger image secret scanning in both source and
bytecode. Replace only their exact values, in the package's generated spec,
without suppressing any scanner rules or touching user credentials.
Source: https://docs.github.com/en/rest/credentials/revoke
"""

import importlib.util
from pathlib import Path


_SHORT = "1234567890abcdef1234567890abcdef12345678"
EXAMPLES = {
    "ghp_" + _SHORT: "<example-personal-access-token>",
    "github_pat_" + "0A1B2C3D4E5F6G7H8I9J0K_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456": "<example-fine-grained-token>",
    "gho_" + _SHORT: "<example-oauth-token>",
    "ghu_" + _SHORT: "<example-github-app-token>",
    "ghr_" + "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab": "<example-refresh-token>",
}


def sanitize(package_dir: Path) -> bool:
    """Replace exact documentation literals and remove their stale bytecode."""
    path = package_dir / "gh_spec.py"
    source = path.read_text(encoding="utf-8")
    patched = source
    for sample, placeholder in EXAMPLES.items():
        original, replacement = repr(sample), repr(placeholder)
        if patched.count(original) == 1 and replacement not in patched:
            patched = patched.replace(original, replacement)
        elif original not in patched and patched.count(replacement) == 1:
            continue
        else:
            raise ValueError("ghapi credential example changed; reassess documentation cleanup")
    compile(patched, str(path), "exec")
    if patched != source:
        path.write_text(patched, encoding="utf-8")
    for bytecode in (package_dir / "__pycache__").glob("gh_spec.*.pyc"):
        bytecode.unlink()
    return patched != source


if __name__ == "__main__":
    spec = importlib.util.find_spec("ghapi")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("ghapi is missing; reassess documentation cleanup")
    sanitize(Path(next(iter(spec.submodule_search_locations))))
