# Neurodesk module execution under Lightcone

Research snapshot: 2026-07-29. This note answers
[neurodesk/neurodesktop#770](https://github.com/neurodesk/neurodesktop/issues/770)
against the current Neurodesktop image, current Neurocommand/CVMFS layout, and
the released and upstream Lightcone sources.

## Decision

**Current Lightcone cannot execute Neurodesk module recipes with truthful
provenance.** A recipe can be made to work functionally by initializing Lmod,
loading a versioned module, and invoking the exposed command. That is not native
host execution: the command is a generated wrapper which invokes
Singularity/Apptainer against a dated CVMFS container sandbox. Lightcone 0.4.0
does not recognize Apptainer or Singularity, resolves the Neurodesktop image to
`runtime: none`, and has no manifest or staleness hooks for the resolved
modulefile, wrapper, CVMFS artifact, runtime, or called script.

The production route should therefore be **first-class, upstream Lightcone
Apptainer execution**, with Neurodesktop modules used only for discovery unless
and until Lightcone has a generic execution-attestation hook. The first pilot
should remove the transparent wrapper from the execution path and have
Lightcone invoke an exact Apptainer artifact directly. It must fail closed when
that artifact cannot be immutably identified.

## What `module load` actually does

Neurodesktop 0fc6c896 pins Apptainer 1.5.3 and installs Neurocommand during the
image build ([Dockerfile runtime pins](https://github.com/neurodesk/neurodesktop/blob/0fc6c896599a9fe60c66c01e3568d84bea9aef80/Dockerfile#L3-L10),
[Neurocommand install](https://github.com/neurodesk/neurodesktop/blob/0fc6c896599a9fe60c66c01e3568d84bea9aef80/Dockerfile#L825-L834)).
At runtime it expands every category below the CVMFS module root into
`MODULEPATH`, so Lmod presents names as `<tool>/<version>`
([environment setup](https://github.com/neurodesk/neurodesktop/blob/0fc6c896599a9fe60c66c01e3568d84bea9aef80/config/jupyter/environment_variables.sh#L19-L45)).
OpenCode commands get an additional `BASH_ENV` bootstrap, but an arbitrary
Lightcone shell must initialize the same environment explicitly
([startup contract](https://github.com/neurodesk/neurodesktop/blob/0fc6c896599a9fe60c66c01e3568d84bea9aef80/docs/architecture.md#L232-L241)).

The current Neurocommand generator creates one wrapper per executable. Each
wrapper runs `singularity ... exec --cleanenv ... <container> <executable>`, and
the generated Lmod file prepends the wrapper directory to `PATH`
([wrapper generation](https://github.com/neurodesk/neurocommand/blob/027e12d3bb1ce9b78b85b8b904736607dd009f6e/neurodesk/transparent-singularity/run_transparent_singularity.sh#L422-L464),
[modulefile generation](https://github.com/neurodesk/neurocommand/blob/027e12d3bb1ce9b78b85b8b904736607dd009f6e/neurodesk/transparent-singularity/run_transparent_singularity.sh#L480-L523)).
The public modulefile is not immutable: reconciliation deliberately rewrites a
stable tool/version entry to the latest kept dated container
([rewrite logic](https://github.com/neurodesk/neurocommand/blob/027e12d3bb1ce9b78b85b8b904736607dd009f6e/cvmfs/reconcile_module_files.py#L100-L122),
[reconciliation plan](https://github.com/neurodesk/neurocommand/blob/027e12d3bb1ce9b78b85b8b904736607dd009f6e/cvmfs/reconcile_module_files.py#L175-L245)).

The effective chain is therefore:

```text
module load tool/version
  -> category/tool/version.lua
  -> PATH prepended with containers/tool_version_builddate/
  -> command resolves to a generated wrapper
  -> wrapper invokes singularity/apptainer exec
  -> dated SIF or expanded CVMFS sandbox
  -> executable inside that container
```

Calling this “host execution” would be inaccurate. The module layer is a
transparent Apptainer launcher.

## Bounded built-image probe

The probe used the locally built `neurodesktop:latest` image without running a
neuroimaging computation. The image was
`sha256:90fde02450acd7daeab4564f1860247d657e4bd13a9f4b4a7f97fa94cda962bd`,
contained Neurocommand commit `027e12d3bb1ce9b78b85b8b904736607dd009f6e`,
and reported Apptainer 1.5.3. A disposable privileged container mounted CVMFS;
`cvmfs_config stat -v` reported root catalog revision `69625` and catalog ID
`bd0537734d072e9b79e9b18033ada57e98214ac0` for this snapshot.

The exact probe was:

```bash
source /opt/neurodesktop/environment_variables.sh
source /usr/share/lmod/lmod/init/bash
module show niimath/1.0.0
module load niimath/1.0.0
command -v niimath
sed -n '1,80p' "$(command -v niimath)"
singularity inspect --json \
  /cvmfs/neurodesk.ardc.edu.au/containers/niimath_1.0.0_20250617/\
niimath_1.0.0_20250617.simg
```

It resolved these identities:

| Layer | Observed identity |
|---|---|
| Module | `niimath/1.0.0` |
| Modulefile | `/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/data_organisation/niimath/1.0.0.lua`; SHA-256 `45ebf3b91c951b8ff4a2f4e22cd4f22cf962d2118d9dd0133e21cfbe99541fb5` |
| Dated build | `niimath_1.0.0_20250617` |
| Wrapper | `/cvmfs/neurodesk.ardc.edu.au/containers/niimath_1.0.0_20250617/niimath`; SHA-256 `060191b7fb6d997d02df4f58f0ddba59f9b36a5cd7baee2f1656962341b2363f` |
| Wrapper command | `singularity --silent exec --cleanenv ... /cvmfs/.../niimath_1.0.0_20250617.simg niimath "$@"` |
| Container artifact | The `.simg` path was an expanded read-only CVMFS sandbox directory, not a regular SIF file, so there was no SIF-file digest to record |
| Embedded build labels | `GITHUB_REPOSITORY=neurodesk/neurocontainers`, `GITHUB_SHA=5cda1a96a59eaf846f2c33cdf33c6488159852b7`, build runtime Apptainer 1.3.4 |

The current catalog metadata agrees that `niimath 1.0.0` selects build date
20250617 ([apps metadata](https://github.com/neurodesk/neurocommand/blob/027e12d3bb1ce9b78b85b8b904736607dd009f6e/neurodesk/apps.json#L2631-L2645),
[published catalog log](https://github.com/neurodesk/neurocommand/blob/027e12d3bb1ce9b78b85b8b904736607dd009f6e/cvmfs/log.txt#L250-L258)).
On this Apple Silicon host, a bounded `singularity exec` stopped with `image
targets 'amd64', cannot run on 'arm64'`; the probe therefore proves resolution
through the wrapper but is not an amd64 execution success claim.

This also shows why `module/version` is insufficient provenance. The truthful
identity must include at least the resolved dated build, modulefile and wrapper
content, runtime and arguments, and either a regular SIF digest or a CVMFS
snapshot/catalog identity for an expanded sandbox.

## What Lightcone 0.4.0 records

Lightcone 0.4.0 was released on 2026-07-24 and its tag is current main commit
`8c1e8187f23aaa401bfdcff256ac18e85d943aa3`
([release](https://github.com/LightconeResearch/lightcone-cli/releases/tag/v0.4.0)).
Its supported local runtimes are only `podman-hpc`, `podman`, and `docker`
([runtime list](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/src/lightcone/engine/container.py#L52-L68));
explicit `apptainer` is rejected and automatic detection falls back to
`runtime: none`
([runtime resolution](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/src/lightcone/engine/container.py#L230-L274)).
The official cluster guide states the same limitation and warns that the
manifest will describe the declared image rather than what executed
([cluster guide](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/docs/user/cluster.md#L76-L85)).

Loading the v0.4.0 source inside the probed Neurodesktop image produced:

```text
runtimes= ('podman-hpc', 'podman', 'docker')
apptainer /usr/local/bin/apptainer
singularity /usr/local/bin/singularity
podman-hpc None
podman None
docker None
detect_runtime= None
load_runtime= RuntimeChoice(runtime='none', explicit=False)
```

With `runtime: none`, the recipe is returned unchanged. If the ASTRA spec
declares a container, `lc run` warns that the declared container will be
ignored but still written to the manifest
([guard](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/src/lightcone/cli/commands.py#L616-L646)).
If the spec declares no container and a module wrapper launches one implicitly,
there is no warning at all: Lightcone sees an uncontained host recipe while
Apptainer actually executes the tool.

The `code_version` hash covers only the authored recipe string, resolved
container identifier, and decisions; it deliberately excludes the runtime
([manifest hash](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/src/lightcone/engine/manifest.py#L110-L133)).
The generated rule passes that hash into staleness detection, but writes the raw
declared container spec into the manifest
([Snakefile configuration](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/src/lightcone/engine/snakefile.py#L420-L458)).
The manifest has `container_image` and, only for Kubernetes workers,
`worker_image`; it has no generic executed-runtime or resolved-artifact field
([manifest fields](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/src/lightcone/engine/manifest.py#L184-L210)).

### Truth and staleness matrix

| Fact | Current manifest | Current stale/verify behavior |
|---|---|---|
| Inline recipe text, including an inline `module load tool/version` | Yes | Changes `code_version` |
| Contents of `bash analysis.sh` / `python analysis.py` | No | Not stale when the file changes; this is confirmed by open upstream [issue #153](https://github.com/LightconeResearch/lightcone-cli/issues/153) |
| Module name/version inside a retained script | No structured field | No effect |
| Resolved modulefile path/content | No | No effect |
| Resolved dated wrapper path/content | No | No effect |
| Actual SIF/sandbox and digest/catalog identity | No | No effect |
| Apptainer/Singularity binary, version, binds, and invocation | No | No effect |
| Git commit | `git_sha` informationally | Not part of `code_version`; dirty-tree state is absent |
| External input file | `mtime-size` by default; directories get SHA-256 | `lc status` only compares `code_version`; `lc verify` skips recomputing external inputs ([verify behavior](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/src/lightcone/engine/verify.py#L92-L110)); Snakemake input/mtime triggers remain the run-time fallback |
| Materialized output bytes and upstream Lightcone outputs | SHA-256/data-version chain | `lc verify` recomputes output bytes and walks manifested upstream outputs |

Consequently, merely embedding `module load niimath/1.0.0` in the command is
not enough. A public modulefile can be repointed without changing that command,
and a retained script can change without changing the command at all.

## Released and upstream Apptainer work

- Released 0.4.0 and current main have no Apptainer/Singularity runtime.
- Open [issue #83](https://github.com/LightconeResearch/lightcone-cli/issues/83)
  is a design for nested Apptainer execution, not released code.
- Open [PR #109](https://github.com/LightconeResearch/lightcone-cli/pull/109)
  adds daemonless `apptainer`/`singularity` branches which build OCI tarballs
  with Buildah and invoke `apptainer exec oci-archive:...`
  ([runtime branch](https://github.com/LightconeResearch/lightcone-cli/blob/abe85cac769694a9fe348da5b47f3f209fa6e9bd/src/lightcone/engine/container.py#L54-L57),
  [recipe wrapping](https://github.com/LightconeResearch/lightcone-cli/blob/abe85cac769694a9fe348da5b47f3f209fa6e9bd/src/lightcone/engine/container.py#L838-L866)).
  As of this snapshot
  it is conflicting with current main and has not been updated since May. It
  targets Lightcone-built OCI archives, not existing Neurodesk SIF/CVMFS module
  artifacts, and leaves `code_version` and the manifest without runtime/module
  attestation
  ([unchanged hash contract](https://github.com/LightconeResearch/lightcone-cli/blob/abe85cac769694a9fe348da5b47f3f209fa6e9bd/src/lightcone/engine/manifest.py#L121-L141)).
  It is reusable implementation material, not a sufficient or
  merge-ready solution.

## Slurm behavior

Released `lc run` must start inside an existing Slurm allocation. It detects
`SLURM_JOB_ID`, starts a scheduler on the allocation driver, and launches one
Dask worker per node using `srun`
([cluster implementation](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/src/lightcone/engine/dask_cluster.py#L413-L462)).
Users can submit an outer `sbatch` script themselves, but released Lightcone
does not submit, persist job state, poll the queue, or cancel jobs. The
manifest records only ambient `slurm_job_id`. The real `srun` branch is mocked
in the released test suite because it requires Slurm
([test boundary](https://github.com/LightconeResearch/lightcone-cli/blob/8c1e8187f23aaa401bfdcff256ac18e85d943aa3/tests/test_dask_cluster.py#L1-L7)).

Draft [PR #160](https://github.com/LightconeResearch/lightcone-cli/pull/160)
adds `lc run --async`, `sbatch`, job records, status polling, and cancellation.
It is still draft, is not in 0.4.0/current main, and its own checklist leaves
real CPU/GPU submission, status transitions, and cancellation unvalidated.
Production readiness therefore still requires the agreed real external Slurm
proof; local or mocked Slurm is only a CI surface.

## Smallest viable upstream seam

The minimum truthful production design is:

1. **A first-class Apptainer runtime adapter.** Resolve an explicit existing
   SIF, expanded sandbox, or immutable ORAS reference before Snakefile
   generation; invoke it directly with `apptainer exec`; fail rather than
   falling back to `none`. Rebase the useful daemonless-runtime work from PR
   #109 onto current main, but add existing-artifact support instead of
   requiring a Buildah-produced OCI archive.
2. **A canonical resolved-execution attestation.** Feed the same canonical
   object into `code_version`, the rule config, the manifest, `lc status`, and
   RO-Crate export. It must record declared reference, actual runtime and
   version, resolved artifact kind/path/immutable identity, and effective
   execution arguments/binds. For a regular SIF the identity can be SHA-256;
   for a CVMFS sandbox it needs repository FQRN plus a path-specific immutable
   catalog/snapshot identity. A root catalog revision alone is truthful but
   over-invalidates every output when unrelated repository content changes.
3. **Generic code dependencies.** Hash declared analysis scripts into
   `code_version` and rerun triggers, closing issue #153. Without this, direct
   Apptainer execution still leaves stale script outputs looking current.
4. **Optional module resolver, not module parsing in the runtime.** A thin
   Neurodesktop adapter may resolve `tool/version` to a descriptor containing
   modulefile hash, dated wrapper hash, artifact identity, and environment
   changes. Lightcone should execute the resolved Apptainer artifact itself.
   If a module cannot be resolved unambiguously, the run must stop. This keeps
   the provenance schema generic while avoiding a Neurodesktop-only executor.

The first pilot can omit item 4 by declaring one exact Apptainer artifact per
recipe and using modules only to discover the container. Multi-module recipes,
where one retained script transparently enters several containers, require the
full resolver/attestation model and are not the smallest viable pilot.

## Newly sharp decisions

1. **CVMFS identity contract:** for expanded sandboxes, choose and prove the
   path-specific immutable catalog identity which Lightcone will store and
   compare. If this cannot be obtained cheaply, pilot with regular digestible
   SIF/ORAS artifacts instead of CVMFS sandboxes.
2. **Pilot execution shape:** choose a single exact container per recipe
   (recommended) or commit to the larger multi-module execution-attestation
   model.
3. **External input identity:** decide whether production manifests require
   strict SHA-256 for every external file or permit immutable dataset IDs;
   current `mtime-size` records and `lc verify` behavior do not meet a strong
   verification claim.
4. **Slurm submission route:** decide whether the pilot waits for a merged,
   released, real-cluster-validated successor to PR #160 or temporarily uses a
   retained outer `sbatch` script while keeping Lightcone's synchronous path
   inside the allocation. Either route still needs real status, cancellation,
   verification, and RO-Crate evidence before production acceptance.
