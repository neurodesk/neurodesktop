# FreeBrowse integration fixtures

`index.ts`, `handlers.py`, and `package.json` are unmodified Jupyter files from
[freesurfer/freebrowse](https://github.com/freesurfer/freebrowse/tree/a42a7ea2e6768fccdabbd39813299a099cd586e4/jupyter),
commit `a42a7ea2e6768fccdabbd39813299a099cd586e4`, the source pinned in the
Dockerfile. The upstream project distributes them under the BSD 2-Clause
license reproduced in `LICENSE`.

`use-file-loading.ts` is the frontend hook from the same commit.

Tests copy these files into a temporary integration, apply Neurodesktop's
build-time patch, and execute the patched frontend and HTTP handler. Update
these fixtures from upstream when changing `FREEBROWSE_REF`.
