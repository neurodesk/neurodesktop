# Lightcone 0.5 execution evidence

Captured on 2026-10-16 from installed `lightcone-cli==0.5.0` using its real
local scheduler and worker, Git, git-annex, and uv 0.12.18. The source archive
SHA-256 is `ebac3ebd1cf5e5466c38af54286590fedbe5c6ba3dede3bd204e53bb7d6c3bfd`.
The image module adapter was installed but this project has no module profile.
Its manifest records the stock Landlock boundary and `fs: declared`.

`project/` contains the author files, canonical manifest, output bytes, and
RO-Crate after `lc materialize CLUSTER baseline/answer`. `never.json` precedes
execution. `current.json` follows execution. A second materialization reported
no work and left the manifest bytes unchanged. `behind.json` follows a committed
`tool.uv.config-settings` edit. `stale.json` follows a committed recipe change
from 42 to 43. The other universe and pending output were never materialized.
Status files were captured outside the project so they could not dirty its tree.

This fixture proves the local worker and evidence contracts. It does not prove
Slurm, CVMFS, FSL, Apptainer, or native arm64 execution. The installed-image
tests exercise those separately.

`module-manifest.json` and `module-refresh-manifest.json` came from a second
real local-worker run with Lmod. A temporary `neurodesk-fixture/1.0` module
provided an external shell command that wrote `module 1.0`. A committed change
to `neurodesk-fixture/1.1` made status `behind`, and `--refresh` produced
`module 1.1`. Both manifests record `fs: open` and their declared pins.
This used the host's real Lmod initializer, not CVMFS or FSL.
