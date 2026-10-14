# FreeBrowse integration fixtures

The integration files and `test_handlers.py` are unmodified copies from
[neurodesk/freebrowse](https://github.com/neurodesk/freebrowse/tree/5f64059c81b7b9eb01ca6a0d261aeb17d27053d2/jupyter),
commit `5f64059c81b7b9eb01ca6a0d261aeb17d27053d2`, pinned by `FREEBROWSE_REF` in the Dockerfile.
The original upstream project distributes the source under the BSD 2-Clause
license reproduced in `LICENSE`.

`use-file-loading.ts` is the frontend hook from the same fork commit.
Tests execute these frozen frontend and HTTP sources directly, without a local
build-time patch. The Hub regression executes JupyterHub's cookie and XSRF
implementation and replaces only the Hub API token lookup.
Update these fixtures and `REF` from the fork when changing `FREEBROWSE_REF`.
