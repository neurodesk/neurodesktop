# Proposal: Neurodesktop for the official Kasm workspace catalog

Neurodesktop provides a Linux desktop for reproducible neuroimaging, with JupyterLab and scientific applications loaded from Neurodesk's CVMFS catalog. This proposal adds an optional Kasm desktop image while retaining the existing Neurodesktop software environment.

Project: https://github.com/neurodesk/neurodesktop

Proposed distribution: `ghcr.io/neurodesk/neurodesktop-kasm`, Linux amd64, Kasm 1.19.x. The generated `release.json` identifies the candidate tag, pinned public base, and source revision. This preparation package does not establish that the image has been published or approved.

## Integration for review

The image derives from Neurodesktop on Ubuntu 24.04. It copies Kasm startup components and the installed KasmVNC package from the digest-pinned `kasmweb/core-ubuntu-noble:1.19.0` donor in the Dockerfile. It runs as UID 1000, serves the desktop on HTTPS port 6901, and keeps Jupyter on loopback inside the session. Persistent profiles use `/home/kasm-user`.

The source bundle contains the actual Dockerfile, startup scripts, license, and runtime tests. Run `source/build.sh` to build its candidate. This differs from workspaces-images' usual Kasm-core final base. Maintainer feedback is requested on accepting an externally maintained image or adapting this source into the upstream build pipeline. No generated upstream CI entry is included because its base assumptions would be incorrect for this derivative.

CVMFS and nested Apptainer execution currently require `privileged: true`. This derivative explicitly enables passwordless sudo for its privileged scientific runtime; it does not change the root Neurodesktop image's package-only sudo policy. The workspace requests four CPU cores, 8 GiB memory, and 1 GiB shared memory. Those are initial resource defaults, not performance guarantees. Slurm is disabled. GPU and arm64 support are outside the proposed initial scope.

## Evidence available from the development image

On 2026-09-19, the local amd64 image `neurodesktop:kasm-1.19.0` passed four desktop/runtime tests and one scientific integration test, plus a persistent-home restart check. Checks covered authenticated HTTPS, LXDE, Jupyter on loopback, CVMFS, and FSL processing through nested Apptainer. The scientific test checked all 210 output voxels after multiplying a generated image by two. ITK-SNAP 4.4.0 displayed the FSL MNI152 reference image in a real browser through a Caddy TLS proxy. The included screenshot records that session.

That local image used a development base. Its evidence does not validate the public-base release candidate. The release workflow rebuilds and repeats automated checks for the exact candidate, generates an SPDX SBOM, scans critical vulnerabilities, and records image identity and publication evidence when requested. Consult those logs before making release claims.

## Required before an official listing

- Test the release image on a real Kasm 1.19 deployment: launch, disconnect/reconnect, stop/resume, deletion, profile persistence, two isolated concurrent users, clipboard, uploads/downloads, and audio. Confirm any unsupported controls are disabled in workspace settings.
- Confirm the image is anonymously pullable and record its registry digest. The workflow checks this after an optional push; a new GHCR package may need its visibility set to public.
- Review the SBOM, component redistribution obligations, and any documented vulnerability exceptions. The repository's MIT license does not replace the licenses of bundled packages or downloaded scientific tools.
- Have Neurodesk maintainers confirm release ownership, security update responsibility, support contact, and the privileged deployment model. Proposed support is the Neurodesktop GitHub issue tracker; this proposal does not assign a person or promise a response time.
- Obtain Kasm maintainer acceptance of the source/distribution approach and catalog metadata. The official store's metadata generator is not a submission endpoint.

This document is a draft for review, not a claim of Kasm certification or an already submitted contribution.
