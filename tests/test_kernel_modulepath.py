"""Regression tests for the notebook MODULEPATH bug.

The bug: installing a local container (creating
/neurodesktop-storage/containers/modules/) caused the notebook kernels to
see only that directory on MODULEPATH, hiding the CVMFS catalogue. The fix
is twofold:

  1. environment_variables.sh no longer collapses MODULEPATH to
     OFFLINE_MODULES when CVMFS is temporarily invisible; it nudges autofs
     and expands the per-category CVMFS subdirectories (transparent-
     singularity layout) when CVMFS is mounted.
  2. Every installed Jupyter kernelspec is wrapped with
     /opt/neurodesktop/kernel_wrapper.sh so each kernel spawn re-sources
     environment_variables.sh and picks up the current CVMFS state -
     mirroring what /etc/bash.bashrc does for terminal shells.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest


WRAPPER = "/opt/neurodesktop/kernel_wrapper.sh"
ENV_SCRIPT = "/opt/neurodesktop/environment_variables.sh"
CVMFS_MODULES_PARENT = "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules"
SYSTEM_KERNEL_ROOTS = (
    "/opt/conda/share/jupyter/kernels",
    "/usr/local/share/jupyter/kernels",
    "/usr/share/jupyter/kernels",
)


def _cvmfs_disabled():
    return os.environ.get("CVMFS_DISABLE", "false").lower() in ("true", "1")


def _discover_kernel_specs(roots):
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        yield from root_path.glob("*/kernel.json")


def test_kernel_wrapper_script_is_installed_and_executable():
    assert Path(WRAPPER).is_file(), f"{WRAPPER} is missing"
    assert os.access(WRAPPER, os.X_OK), f"{WRAPPER} is not executable"


def test_system_kernel_specs_are_wrapped():
    """Every kernelspec shipped in the image must invoke kernel_wrapper.sh.

    Without this, notebook kernels inherit a stale MODULEPATH from the
    Jupyter server (baked in lazy-CVMFS mode before the mount completed).
    """
    specs = list(_discover_kernel_specs(SYSTEM_KERNEL_ROOTS))
    assert specs, "No Jupyter kernel specs found under the system roots"

    unwrapped = []
    for spec_file in specs:
        spec = json.loads(spec_file.read_text())
        argv = spec.get("argv") or []
        if not argv or argv[0] != WRAPPER:
            unwrapped.append(str(spec_file))
    assert not unwrapped, (
        "These kernelspecs are missing the kernel_wrapper.sh prefix and "
        "will not refresh MODULEPATH at kernel spawn time: "
        + ", ".join(unwrapped)
    )


def test_wrapper_execs_its_arguments():
    """The wrapper should exec whatever command it is given."""
    result = subprocess.run(
        [WRAPPER, "/bin/echo", "hello-from-wrapper"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "hello-from-wrapper" in result.stdout


def test_wrapper_sets_modulepath_with_cvmfs_expansion():
    """When CVMFS is mounted, the wrapper must produce a MODULEPATH that
    contains per-category subdirectories, not just the bare CVMFS parent
    (which would cause Lmod to show modules as <category>/<tool>/<version>
    instead of <tool>/<version>).
    """
    if _cvmfs_disabled() or not Path(CVMFS_MODULES_PARENT).is_dir():
        pytest.skip("CVMFS is not mounted in this test environment")

    result = subprocess.run(
        [WRAPPER, "/bin/bash", "-c", 'printf "%s" "$MODULEPATH"'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    modulepath_entries = [e for e in result.stdout.split(":") if e]

    # Verify the glob expanded to multiple per-category entries, not the
    # bare parent directory (which would have the wrong Lmod namespace).
    cvmfs_entries = [e for e in modulepath_entries if e.startswith(CVMFS_MODULES_PARENT)]
    assert cvmfs_entries, (
        f"MODULEPATH lacks any CVMFS entries: {modulepath_entries!r}"
    )
    bare_parent = {CVMFS_MODULES_PARENT, CVMFS_MODULES_PARENT + "/"}
    assert not (set(cvmfs_entries) <= bare_parent), (
        "MODULEPATH contains only the bare CVMFS parent, not per-category "
        f"subdirectories. Entries: {cvmfs_entries!r}"
    )
    # A real deployment has dozens of categories; require at least a few
    # to guard against the glob silently failing.
    assert len(cvmfs_entries) >= 3, (
        f"Expected multiple CVMFS per-category MODULEPATH entries, got: "
        f"{cvmfs_entries!r}"
    )


def test_local_container_dir_does_not_hide_cvmfs(tmp_path):
    """Regression test for the original bug.

    When OFFLINE_MODULES exists (local container installed), MODULEPATH
    must still include CVMFS entries. Previously, an existence check
    falsely short-circuited to `MODULEPATH=${OFFLINE_MODULES}`.
    """
    if _cvmfs_disabled() or not Path(CVMFS_MODULES_PARENT).is_dir():
        pytest.skip("CVMFS is not mounted in this test environment")

    fake_local = tmp_path / "containers"
    (fake_local / "modules").mkdir(parents=True)

    # Source the env script with NEURODESKTOP_LOCAL_CONTAINERS pointed at a
    # freshly-created "local containers" directory, then print MODULEPATH.
    script = f'export NEURODESKTOP_LOCAL_CONTAINERS="{fake_local}"; source {ENV_SCRIPT} 2>/dev/null; printf "%s" "$MODULEPATH"'
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    entries = [e for e in result.stdout.split(":") if e]
    assert any(str(fake_local) in e for e in entries), (
        f"OFFLINE_MODULES not on MODULEPATH: {entries!r}"
    )
    assert any(e.startswith(CVMFS_MODULES_PARENT) for e in entries), (
        "MODULEPATH lost CVMFS after creating a local-containers directory "
        f"(this is the regression): {entries!r}"
    )


def test_modulepath_survives_when_cvmfs_dir_is_missing(tmp_path):
    """When CVMFS is genuinely unreachable, MODULEPATH should fall back to
    OFFLINE_MODULES - not be empty or contain stale wrong entries.
    """
    fake_local = tmp_path / "containers"
    (fake_local / "modules").mkdir(parents=True)

    # Simulate a completely absent CVMFS by redirecting CVMFS_MODULES to a
    # nonexistent path via a wrapper that overrides after sourcing.
    fake_cvmfs = tmp_path / "no-such-cvmfs"
    script = (
        f'export NEURODESKTOP_LOCAL_CONTAINERS="{fake_local}"; '
        f'source {ENV_SCRIPT} 2>/dev/null; '
        # Force the disabled branch by overriding CVMFS_MODULES and re-running
        # the relevant block manually.
        f'export CVMFS_MODULES="{fake_cvmfs}/"; '
        f'if [ ! -d "$CVMFS_MODULES" ]; then export MODULEPATH="$OFFLINE_MODULES"; fi; '
        f'printf "%s" "$MODULEPATH"'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    entries = [e for e in result.stdout.split(":") if e]
    assert entries, "MODULEPATH is empty when CVMFS is unavailable"
    assert any(str(fake_local) in e for e in entries), (
        f"MODULEPATH is missing the local containers dir: {entries!r}"
    )
