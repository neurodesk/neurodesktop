---
title: Publishing the Kasm workspace
description: Release artifacts, verification gates, and the official catalog proposal.
parent: kasm.md
status: current
last-reviewed: 2026-09-21
---

# Publishing the Kasm workspace

The [Kasm image](kasm.md) has a manual release workflow and a reproducible submission bundle. Publication to GHCR and acceptance into the official Kasm catalog are separate steps. The first GHCR release passed runtime checks and vulnerability scanning on 2026-09-21. Official catalog acceptance remains pending.

## Release files

- [Release workflow](../../.github/workflows/release-kasm.yml): builds amd64, runs desktop and scientific checks, creates an SPDX SBOM, scans critical vulnerabilities using the repository's exception policy, and optionally pushes the tested image.
- [Workspace metadata](../../config/kasm/publishing/workspace.json): schema 1.1 template. Its compatibility array is populated by the generator; do not distribute the template directly.
- [Submission generator](../../scripts/prepare_kasm_submission.py): packages the metadata, existing Neurodesk artwork, actual desktop screenshot, buildable source, proposal, provenance, and SHA-256 checksums.
- [Maintainer proposal](../../config/kasm/publishing/submission.md): describes the integration, existing evidence, and outstanding review decisions.

The screenshot shows the development image running ITK-SNAP with the FSL MNI152 reference. It is illustrative evidence, not a screenshot of a validated Kasm control-plane session. The square SVG reuses the repository's Neurodesk brain icon.

## Prepare a candidate

Use a publicly pullable Neurodesktop Ubuntu 24.04 digest, not a local image ID. The public `amd64` manifest resolved on 2026-09-19 to the digest below. This base has not yet passed the derivative's release workflow.

```bash
python3 scripts/prepare_kasm_submission.py \
  --image ghcr.io/neurodesk/neurodesktop-kasm:1.19.0-20260919.1 \
  --base-image ghcr.io/neurodesk/neurodesktop@sha256:80417277ff8062aba9029df7553ce406ab0719ffd8e154f90f5248052a7b743d \
  --uncompressed-size-mb 11330 \
  --output /tmp/neurodesktop-kasm-submission
cd /tmp/neurodesktop-kasm-submission
sha256sum -c SHA256SUMS
```

The 11,330 MB value is a conservative development-image estimate from Docker disk usage. The workflow replaces it with the candidate's exported root filesystem size, rounded up to decimal MB. Do not use compressed `.Size` from Docker's containerd image store as an uncompressed-size measurement.

The generator requires a new output directory, a versioned `1.19.0-YYYYMMDD.N` tag, and a public base digest. It copies only an explicit set of source files. No session password, host proxy configuration, or local runtime environment file belongs in the package. `release.json` always describes a review candidate; publication proof is recorded separately in workflow evidence.

## Build and release

Pushing a Git tag named `kasm-1.19.0-YYYYMMDD.N` also builds, tests, and publishes that release. This works before the workflow reaches the default branch. Use a new release number for each published image.

Commit the reviewed source and open **Actions → Build and publish Kasm image → Run workflow**. Publication is enabled by default; clear `publish` for a build-and-test dry run. Leave the base and tag inputs blank to resolve the current public `amd64` base to a digest and generate `1.19.0-YYYYMMDD.RUN_NUMBER`. Supply an explicit public base digest and unused versioned tag when you need to select them yourself. The image is published to `ghcr.io/OWNER/neurodesktop-kasm:TAG` using the repository’s `GITHUB_TOKEN`; no separate registry secret is needed. The workflow summary includes the pull command. The workflow requires a runner with enough disk for the base, donor, resulting image, and scan cache; use a larger amd64 runner if a standard hosted runner runs out of space. Scientific checks need privileged Docker and outbound CVMFS/software-download access. A failure blocks publication and retains available logs.

Review the resulting `neurodesktop-kasm-TAG` artifact. Its evidence includes build logs, both test modes, image inspection, root filesystem size, SBOM, and vulnerability results. Vulnerability exceptions require the same review as the root image's policy. Do not add blanket ignores to make the gate pass.

After reviewing the candidate, run with `publish=true` to rebuild, retest, and push. The workflow refuses existing tags and serializes all Kasm releases. Protect package writes from other release processes as GHCR does not make tags immutable. A new GHCR package can default to private; set the package visibility to public and verify anonymous access before submission. If the push succeeds but the visibility check fails, preserve that tag and validate its anonymous manifest once visibility is corrected. Do not overwrite it by rerunning publication.

Record the digest in `evidence/published-digest.json`. The source label links the package to its GitHub repository. The GitHub token needs permission to create or write that organization's GHCR package. A successful push does not add the image to Kasm's catalog.

## Official catalog submission

Kasm's [image source repository](https://github.com/kasmtech/workspaces-images) accepts source contributions. The [registry template](https://github.com/kasmtech/workspaces_registry_template) creates third-party stores. The official store's [New page](https://registry.kasmweb.com/1.1/new/) generates metadata; it does not submit an application. Research did not find a public listing-only submission policy for externally maintained images.

Use the prepared proposal to establish the accepted distribution route with Kasm maintainers. If they request an upstream source PR, adapt the build to their conventions in `workspaces-images`, including its CI manifest generator. The current derivative has Neurodesktop as its final base, so adding a conventional `core-ubuntu-noble` CI entry would misrepresent the build. Keep that difference explicit.

Before requesting a listing, complete the real Kasm platform test matrix in the proposal and attach results for the published digest. Confirm maintainer ownership and component licensing. The store metadata must retain the privileged-mode requirement. Only advertise additional architectures or Kasm versions after testing them.

See [testing](../testing.md) for the two test tiers and the [original assessment](../designs/kasm-workspace-assessment.md) for background.
