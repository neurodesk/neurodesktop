---
title: Kasm image
description: Build and run the Neurodesktop image variant for Kasm Workspaces
parent: ../architecture.md
status: current
last-reviewed: "2026-09-21"
---

# Kasm image

Part of [Architecture](../architecture.md). The separate
[Kasm Dockerfile](../../config/kasm/Dockerfile) layers Kasm's Ubuntu Noble
runtime onto an existing Ubuntu 24.04 Neurodesktop image. It targets Kasm
Workspaces 1.19.0. The root Dockerfile remains the Jupyter-first image.

For release automation, store metadata, and the official catalog proposal, see
[Publishing the Kasm workspace](kasm-publishing.md).

## Build the image

Run from the repository root, substituting your Neurodesktop image tag or digest:

```bash
docker build -f config/kasm/Dockerfile \
	--build-arg NEURODESKTOP_IMAGE=neurodesktop:local \
	-t neurodesktop:kasm-1.19.0 .
```

The Kasm donor image is pinned by digest. The build repackages its installed
KasmVNC package, installs that package with its dependencies, and copies its
startup and supporting services. It retains Neurodesktop's LXDE desktop,
Conda environment, Apptainer, and scientific software configuration.

## Run and verify locally

Set a password, then start the image:

```bash
read -rs VNC_PW
export VNC_PW
docker run --rm --name neurodesktop-kasm --shm-size=1g \
	-p 127.0.0.1:6901:6901 -e VNC_PW -e CVMFS_DISABLE=true \
	-v neurodesktop-kasm-home:/home/kasm-user \
	neurodesktop:kasm-1.19.0
```

Open `https://localhost:6901` and accept the local self-signed certificate.
Sign in as `kasm_user` with the password you supplied. This command tests the
desktop without mounting CVMFS. It does not prove nested scientific tools can
run under the host's container restrictions.

Run the repeatable smoke test with access to Docker:

```bash
bash scripts/verify_kasm_image.sh neurodesktop:kasm-1.19.0
```

The test creates a temporary container and home volume, checks desktop and
Jupyter readiness and Kasm authentication, then recreates the container to
verify home persistence. It removes its containers and volume on exit. See
[Testing](../testing.md) for the test tiers.

To also verify CVMFS and nested FSL execution, run the explicit privileged mode:

```bash
bash scripts/verify_kasm_image.sh neurodesktop:kasm-1.19.0 --science
```

This mode starts Docker with `--privileged`, enables CVMFS, and checks that
FSL doubles every voxel in a generated NIfTI image. It requires authorization
to grant the test container broad host capabilities. The ordinary smoke test
does not request those capabilities.

## Configure Kasm Workspaces

Add a Container Workspace with the image tag above. The image must be available
on each Kasm Agent; push it to your own Docker registry for a multi-agent
installation. Kasm supplies the session password. Set shared memory to 1 GiB
through Docker Run Config, for example `{"shm_size": 1073741824}`. Choose CPU
and RAM limits for the scientific workload; 4 CPUs and 8 GiB RAM are starting
values to measure, not guarantees for every tool.

Set the persistent profile destination to `/home/kasm-user`. Give every user
their own profile storage. Mount datasets and `/neurodesktop-storage`
separately. CVMFS and nested Apptainer require the same host capabilities as
the base Neurodesktop image; desktop streaming does not provide those
capabilities. Validate the chosen device, mount, and privilege settings with
a real tool before offering the workspace to users.

On Ubuntu 24.04 agents, AppArmor's restriction on unprivileged user namespaces
can block nested Apptainer with `Could not write info to setgroups: Permission denied`,
even when Docker uses privileged mode. Follow Apptainer's
[host requirements](https://apptainer.org/docs/admin/main/installation.html#running-inside-docker).
On a dedicated agent, the documented host setting is
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.
This changes host policy for all processes, so account for it when selecting
agents for this workspace. The release workflow applies it temporarily on its
disposable runner and restores the original value after scientific testing.

The initial variant includes desktop streaming, clipboard, uploads, downloads,
and audio services. Printer, smartcard, gamepad, webcam, and recording services
are disabled by default. GPU use and platform-managed profile synchronization
need deployment-specific verification.

## Startup and identity

The final user is `jovyan`, UID 1000. Its home is `/home/kasm-user`, and
`/home/jovyan` links there for existing Neurodesktop paths. Kasm initializes a
fresh home from `/home/kasm-default-profile`. The entrypoint runs Neurodesktop's
existing bootstrap through sudo, then starts KasmVNC. This derivative explicitly enables
passwordless sudo for its privileged scientific runtime. The root Neurodesktop
image's package-only sudo policy remains unchanged.

Kasm's custom startup hook launches LXDE and JupyterLab. Jupyter listens only
on container loopback port 8888 and retains token authentication. The desktop's
JupyterLab shortcut opens the current token URL inside Firefox. The Jupyter
launcher entries for the alternate VNC and RDP desktops are removed in this
variant so Kasm owns the desktop session.

The health check requires KasmVNC, LXDE, and Jupyter. Runtime variables and
build arguments are listed in [Environment variables](../environment-variables.md#kasm-image).
The original tradeoffs are recorded in the
[Kasm assessment](../designs/kasm-workspace-assessment.md).

## Validation scope

The initial amd64 build used the local `neurodesktop:package-upgrade` base.
Three unit tests and four container tests passed. The smoke test also verified
home persistence across container recreation. A browser session displayed
the Neurodesktop desktop and opened JupyterLab through its desktop shortcut.

On 2026-09-19, privileged validation mounted CVMFS and ran FSL 6.0.7.22 through
nested Apptainer. Every output voxel in the 210-voxel arithmetic test matched
the expected value. The repeatable `--science` smoke test passed all five
tests and verified home persistence across container recreation.
ITK-SNAP 4.4.0 also opened FSL's bundled MNI152 reference brain through a
browser KasmVNC session, using Mesa software OpenGL rendering.

On 2026-09-21, the [GitHub release run](https://github.com/neurodesk/neurodesktop/actions/runs/35630163360)
built on the public Neurodesktop digest, passed both runtime test modes and
persistent-home recreation, generated an SBOM, passed the critical vulnerability
scan with the repository exception policy, and published to GHCR with anonymous
manifest access verified.

Kasm Workspaces control-plane integration, GPU support, and arm64 remain
unverified. The standalone smoke test does not establish those capabilities.
