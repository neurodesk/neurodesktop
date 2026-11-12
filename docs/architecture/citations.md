---
title: Tool citations
description: Lmod usage recording and the neurodesk-cite command that writes
  CITATIONS.md for the Neurodesk tools a user loaded
parent: ../architecture.md
status: current
last-reviewed: "2026-11-12"
---

# Tool citations

Part of [Architecture](../architecture.md). Related environment variables are
listed in [Environment variables](../environment-variables.md#cvmfs-and-modules).

Users run `neurodesk-cite` to get a `CITATIONS.md` file for the Neurodesk tools
they loaded. The file has one methods sentence per tool version, in the format
of the website's **Copy methods text** button, and a deduplicated reference
list that ends with the Neurodesk paper. The interactive terminal banner
mentions the command.

```bash
neurodesk-cite                         # writes CITATIONS.md in neurodesktop-storage
neurodesk-cite --since 2026-11-01      # only loads on or after this date
neurodesk-cite --output paper/CITATIONS.md
neurodesk-cite --clear                 # empty the usage log
```

## Recording loads

The image sets `LMOD_PACKAGE_PATH=/opt/neurodesktop/lmod`, so Lmod loads
[`config/lmod/SitePackage.lua`](../../config/lmod/SitePackage.lua) for every
module command. Its `load` hook appends `name<TAB>version<TAB>UTC timestamp`
to `$XDG_STATE_HOME/neurodesk/module-usage.tsv`, which defaults to
`~/.local/state/neurodesk/module-usage.tsv`. The home directory is the
persistent volume in the documented Docker and hosted setups, so the log
survives restarts.

The hook records only modulefiles under `/cvmfs/neurodesk.ardc.edu.au/`,
`$NEURODESKTOP_LOCAL_CONTAINERS/modules/`, or the
`/neurocommand/local/containers/modules/` link that desktop launchers use.
It runs inside `pcall`, makes no network request and writes one line, so a
failure such as an unwritable home leaves `module load` unaffected.

Lmod runs the hook for each load path:

- `ml` and `module load` in terminals.
- Notebook kernels and the jupyter-lmod side panel, which run `$LMOD_CMD python
  load` in a process that inherits the image environment.
- Applications-menu launchers, which call neurocommand's `fetch_and_run.sh`,
  which runs `module load`.

Lmod also runs the hook when it reloads an already loaded module, so the log
can contain repeated lines. `neurodesk-cite` deduplicates them.

## Writing CITATIONS.md

[`config/jupyter/neurodesk_cite.py`](../../config/jupyter/neurodesk_cite.py) is
installed as `/usr/local/bin/neurodesk-cite` and uses only the standard
library. It fetches the website catalogue at
`https://neurodesk.org/data/applist.json` and caches it in
`$XDG_CACHE_HOME/neurodesk/applist.json`. When the fetch fails, it uses the
cache and reports the cache date. Without a cache it still writes the file,
without build dates, and reports that.

A module `name/version` matches the catalogue entry whose `application` is
`name_version_YYYYMMDD`. When several builds match, the newest build date is
used. The entry supplies the build date, the `citation` text, which comes from
the neurocontainers recipes, and the `electrophysiology` category, which adds
the AEDAPT note. A tool without a citation, or a module missing from the
catalogue, gets a pointer to the
[citation guide](https://neurodesk.org/overview/how-to-cite-us/) and its
neurocontainers recipe. The methods sentence names the image's
`NEURODESKTOP_VERSION`.

The default output is `CITATIONS.md` in `~/neurodesktop-storage`, then
`/neurodesktop-storage`, the first that is a writable directory, otherwise in
the home directory.

## Tests

`pytest tests/unit/test_neurodesk_cite.py` runs the command against a fixture
catalogue. `pytest tests/unit/test_module_usage_hook.py` loads modules through
the real Lmod with the site package; it skips when Lmod is not installed, and
the unit-test workflow installs it. In the image,
`tests/container/test_module_usage_image.py` checks the installed hook and
command together.
