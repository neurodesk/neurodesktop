# FreeBrowse integration fixtures

The integration files and `test_handlers.py` (copied from `tests/test_jupyterhub_auth.py`) are unmodified copies from
[neurodesk/freebrowse](https://github.com/neurodesk/freebrowse/tree/c5b3e6195fcc00ae901bdfb5592ea0e954da6f95/jupyter),
commit `c5b3e6195fcc00ae901bdfb5592ea0e954da6f95`, pinned by `FREEBROWSE_REF` in the Dockerfile.
The original upstream project distributes the source under the BSD 2-Clause
license reproduced in `LICENSE`.

`use-file-loading.ts` is the frontend hook from the same fork commit.
Tests execute these frozen frontend and HTTP sources directly, after applying `config/jupyter/configure_freebrowse_defaults.py`, the same
build-time adjustment that keeps FreeBrowse optional for volumes. The Hub regression executes JupyterHub's cookie and XSRF
implementation and replaces only the Hub API token lookup.
Update these fixtures and `REF` from the fork when changing `FREEBROWSE_REF`.
