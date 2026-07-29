"""Shared helpers for both test tiers.

``tests/unit/`` runs against a repository checkout with no container: it covers
repository sources, importable Python modules, and shell scripts driven against
a temporary ``HOME``. ``tests/container/`` runs inside the built image, where it
is installed at ``/opt/tests/``, and covers only what a running container can
answer: mounts, installed kernels, services, and CVMFS.

Both tiers resolve their subject through the helpers here so that no test has to
know which layout it is running in.
"""

import importlib.util
import os
import subprocess
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

#: ``/opt/tests`` in the image, ``<checkout>/tests`` on a developer machine.
IN_IMAGE = TESTS_DIR == Path("/opt/tests")

#: The repository checkout, or ``None`` when running from the installed copy.
REPO_ROOT = None if IN_IMAGE else TESTS_DIR.parent


def repo_path(relative):
    """Absolute path to *relative* inside the repository checkout.

    Only valid in the unit tier; the checkout does not exist inside the image.
    """
    if REPO_ROOT is None:
        raise AssertionError(
            f"{relative} is a repository source and is not available inside the "
            "image; this test belongs under tests/unit/"
        )
    return REPO_ROOT / relative


def first_existing_path(*candidates):
    """Return the first candidate that exists, or raise listing all of them."""
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    checked = ", ".join(str(candidate) for candidate in candidates)
    raise AssertionError(f"None of these paths exist: {checked}")


def resolve_source(installed, relative):
    """Resolve a file that the image installs and the checkout also carries.

    *installed* is the absolute path the Dockerfile installs it to — or a
    sequence of them, tried in order, for files with a generated variant that
    should win over the shipped template. *relative* is the path inside the
    repository. The installed copy wins so that container tests assert against
    what actually shipped; the checkout is the fallback so the same test runs in
    the unit tier.
    """
    if isinstance(installed, (str, Path)):
        installed = [installed]
    candidates = [Path(candidate) for candidate in installed]
    if REPO_ROOT is not None:
        candidates.append(REPO_ROOT / relative)
    return first_existing_path(*candidates)


def load_source_module(name, installed, relative):
    """Import a Python source file that ships both installed and in the checkout.

    Resolution follows :func:`resolve_source`. The resolved path is recorded on
    the module as ``SOURCE_PATH`` so tests can assert against the file itself.
    """
    path = resolve_source(installed, relative)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SOURCE_PATH = path
    return module


def run_cmd(cmd, cwd=None, env=None, timeout=None):
    """Run *cmd* through the shell, returning ``(exit_code, combined_output)``.

    *env* overlays the caller's environment rather than replacing it, so a test
    can override one variable without having to rebuild ``PATH``.
    """
    merged_env = None
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update(env)
    process = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        env=merged_env,
        timeout=timeout,
    )
    return process.returncode, process.stdout.strip()
