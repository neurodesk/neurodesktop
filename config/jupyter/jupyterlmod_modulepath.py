"""Refresh jupyter-lmod MODULEPATH after lazy CVMFS startup.

The jupyter-lmod side panel runs inside the long-lived Jupyter server process.
In lazy CVMFS mode that process can start before CVMFS is mounted, leaving its
MODULEPATH with only local modules. Kernels and terminals re-source
environment_variables.sh later, but jupyter-lmod reads os.environ directly.
"""

import functools
import inspect
import os
from glob import glob


DEFAULT_LOCAL_CONTAINERS = "/neurodesktop-storage/containers"
DEFAULT_CVMFS_MODULES = "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/"


def _split_modulepath(value):
    return [entry for entry in value.split(":") if entry]


def _append_missing(entries, new_entries):
    seen = set(entries)
    for entry in new_entries:
        if entry and entry not in seen:
            entries.append(entry)
            seen.add(entry)
    return entries


def refresh_modulepath():
    """Merge Neurodesk local/CVMFS module roots into this process.

    This intentionally avoids replacing MODULEPATH wholesale. jupyter-lmod lets
    users edit MODULEPATH from the side panel, and Lmod module loads may also
    mutate it. The stale-startup bug only needs missing CVMFS roots to be added
    once they become visible.
    """

    local_containers = os.environ.get(
        "NEURODESKTOP_LOCAL_CONTAINERS", DEFAULT_LOCAL_CONTAINERS
    )
    offline_modules = os.path.join(local_containers, "modules") + "/"
    cvmfs_modules = os.environ.get("CVMFS_MODULES", DEFAULT_CVMFS_MODULES)
    if not cvmfs_modules.endswith("/"):
        cvmfs_modules += "/"

    entries = _split_modulepath(os.environ.get("MODULEPATH", ""))

    # Nudge autofs/lazy CVMFS and then expand the transparent-singularity
    # category layout, matching environment_variables.sh.
    try:
        os.listdir(cvmfs_modules)
    except OSError:
        cvmfs_entries = []
    else:
        cvmfs_entries = sorted(glob(os.path.join(cvmfs_modules, "*")))

    if os.path.isdir(offline_modules):
        _append_missing(entries, [offline_modules])

    if cvmfs_entries:
        _append_missing(entries, cvmfs_entries)
        os.environ["CVMFS_DISABLE"] = "false"
    elif os.path.isdir(offline_modules):
        if not entries:
            entries = [offline_modules]
        os.environ["CVMFS_DISABLE"] = "true"

    if entries:
        os.environ["MODULEPATH"] = ":".join(entries)

    os.environ["NEURODESKTOP_LOCAL_CONTAINERS"] = local_containers
    os.environ["OFFLINE_MODULES"] = offline_modules
    os.environ["CVMFS_MODULES"] = cvmfs_modules
    return entries


class _RefreshingModuleAPI:
    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __getattr__(self, name):
        attr = getattr(self._wrapped, name)
        if not callable(attr):
            return attr

        @functools.wraps(attr)
        async def wrapper(*args, **kwargs):
            refresh_modulepath()
            result = attr(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        return wrapper


def install():
    """Patch jupyter-lmod so each request sees the current CVMFS MODULEPATH."""

    try:
        import jupyterlmod.handler as handler
    except Exception:
        return False

    if not isinstance(handler.MODULE, _RefreshingModuleAPI):
        handler.MODULE = _RefreshingModuleAPI(handler.MODULE)

    if not getattr(handler.ModulePaths, "_neurodesktop_modulepath_refresh", False):
        original_get = handler.ModulePaths.get

        @functools.wraps(original_get)
        async def get_with_refresh(self, *args, **kwargs):
            refresh_modulepath()
            return await original_get(self, *args, **kwargs)

        handler.ModulePaths.get = get_with_refresh
        handler.ModulePaths._neurodesktop_modulepath_refresh = True

    return True
