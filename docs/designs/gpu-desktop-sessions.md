---
title: GPU desktop session assessment
description: Assess VirtualGL EGL rendering for the existing Neurodesktop
  desktop, nested scientific containers, and GPU deployment profiles
parent: index.md
status: proposed
last-reviewed: "2026-10-05"
---

# GPU desktop session assessment

This is research and a proposed implementation path. No GPU desktop support
was implemented or exercised on GPU hardware for this assessment.

The recommended first experiment is an opt-in NVIDIA/amd64 session that keeps
LXDE, TigerVNC, and Guacamole, and launches selected scientific applications
through VirtualGL's EGL backend. This tests the technique in the supplied
FreeSurfer example without replacing the desktop transport. Extend coverage
to RDP, arm64, and Kasm only after the first application's complete launch path
works, including its nested Apptainer container.

## What the FreeSurfer example does

The supplied [FreeSurfer Dockerfile](https://github.com/pwighton/fs-docker/blob/7054d7eee0590597c8da1ca5509eeb599d6fdeae/freesurfer/Dockerfile.8.2.0-z2jh)
uses an ordinary XFCE/TigerVNC desktop with Jupyter remote desktop proxy. It
installs VirtualGL 3.1.5, sets `NVIDIA_DRIVER_CAPABILITIES=all`, and adds shell
aliases for `freeview` and `tkmeditfv` that run `vglrun -d egl0` when
`nvidia-smi` succeeds. It also disables XFCE compositing. NVIDIA driver libraries
are expected to arrive through the worker's container runtime.

The aliases redirect application rendering. The Dockerfile does not start a
GPU-backed Xorg server, replace TigerVNC with TurboVNC, configure hardware video
encoding, or allocate a Kubernetes GPU. These remain separate concerns. Its
FreeSurfer executables are installed directly into the desktop image, so it
does not demonstrate Neurodesk's nested scientific-container case. Shell
aliases also do not rewrite `.desktop` launchers. The example's `nvidia-smi`
gate checks driver access but does not test EGL context creation.

## Fit with the existing desktop

The current [desktop](../architecture/desktop.md) has separate VNC and RDP
sessions behind Guacamole. The
[`Xtigervnc` wrapper](../../config/lxde/Xtigervnc) confines the virtual X server
child to Mesa's EGL vendor to avoid crashes from injected NVIDIA libraries.
It leaves desktop applications' vendor selection alone. Preserve that scope.
Removing the wrapper or exporting its Mesa restriction across the whole
desktop would undermine this design.

VirtualGL intercepts an application's GLX/OpenGL calls, renders on the server
GPU, and copies frames into the application's existing X window. Its EGL
backend needs a usable EGL device rather than a separate GPU X server.
`egl0` or a DRI device path selects that backend. The local X proxy receives
the resulting pixels, then the existing remote desktop stack streams them.
EGL emulates part of GLX, so application compatibility must be tested. See the
[VirtualGL 3.1.5 guide](https://raw.githubusercontent.com/VirtualGL/virtualgl/3.1.5/doc/index.html),
sections 3, 6.3, 6.4, and 9.1. This does not establish acceleration for native
EGL, Vulkan, or browser WebGL applications; test those paths separately.

The resulting proposed flow is:

```text
Scientific app in its tool container
    -> VirtualGL interposer in that application process
    -> assigned NVIDIA GPU through EGL
    -> pixels in the user's TigerVNC X window
    -> Guacamole through authenticated Jupyter
    -> browser
```

CUDA computation, application OpenGL rendering, and desktop streaming/encoding
have different requirements. Accelerating the rendering stage can still leave
frame copies, CPU encoding, bandwidth, or browser decoding as the limiting
stage. The [VirtualGL background](https://virtualgl.org/About/Background)
describes these stages and why VNC encoding affects interactive 3D workloads.
Measure end-to-end interaction before proposing another transport.

## Runtime and GPU allocation

The host owns the NVIDIA kernel driver and container runtime integration.
The image needs VirtualGL and the application's client libraries, not an
embedded host kernel driver. NVIDIA Container Toolkit defaults to
`compute,utility`; OpenGL/EGL needs `graphics`. For an initial configuration,
request `compute,utility,graphics,display` and narrow it after testing the
headless EGL path. `display` covers X11/Wayland display use, while `video` is
for video codec support and is not inherently needed for VirtualGL rendering.
Capability lists replace defaults, so retain `compute` if scientific CUDA
workloads need it. See
[NVIDIA's capability reference](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html).

A deployment sketch for a future GPU-enabled image is:

```bash
docker run --gpus device=GPU-ASSIGNED-UUID \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,display \
  YOUR_GPU_ENABLED_NEURODESKTOP_IMAGE
```

This only illustrates device and driver injection. It omits the deployment's
existing volumes, Jupyter authentication, ports, and CVMFS settings. It does
not make the current image launch applications through VirtualGL.

For Z2JH, offer an explicit GPU profile rather than reserving a GPU for every
desktop. The [Jupyter resource guide](https://z2jh.jupyter.org/en/stable/jupyterhub/customizing/user-resources.html#set-user-gpu-guarantees-limits)
documents `kubespawner_override.extra_resource_limits`:

```yaml
singleuser:
  profileList:
    - display_name: "Neurodesktop GPU prototype"
      kubespawner_override:
        extra_resource_limits:
          nvidia.com/gpu: "1"
        environment:
          NVIDIA_DRIVER_CAPABILITIES: compute,utility,graphics,display
```

Node drivers, NVIDIA runtime integration, and a device plugin must already be
configured. Kubernetes GPU limits become requests if omitted; when both are
specified they must match. Select graphics-capable workers and account for
taints and cluster-specific runtime configuration. See
[Kubernetes GPU scheduling](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/).

Do not assume that a CUDA-capable allocation supports graphics. MIG and GPU
model/profile support differ; newer NVIDIA hardware even has explicit graphics
MIG profiles. Verify the actual allocation with an EGL probe rather than
classifying every MIG device identically. See
[NVIDIA's supported GPU/profile documentation](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-gpus.html).

## Nested Apptainer is the main integration question

[`configure_apptainer_nv`](../../config/jupyter/environment_variables.sh)
already defaults `APPTAINER_NV=1` when the proprietary driver is loaded and
preserves explicit overrides, including `0`. The
[Sherlock launcher](../../scripts/connectSherlock.sh) also supports NVIDIA
passthrough. These establish existing GPU access, not VirtualGL application
rendering. [Neurocommand](../architecture/cvmfs.md#neurocommand) owns the
scientific tool launchers and generated module/menu entries.

Apptainer `--nv` discovers host driver libraries through `nvbliblist` and
`ldconfig`, binds devices/libraries, and sets the container's library path.
Host libraries can fail against an older tool container's libc. It normally
exposes all accessible NVIDIA devices; `CUDA_VISIBLE_DEVICES` filters CUDA
applications, not EGL device selection or a security boundary. Its experimental
`--nvccli` path supports finer device selection but has writable-image and
execution-mode restrictions. See the
[Apptainer GPU guide](https://apptainer.org/docs/user/latest/gpu.html).

In nested Docker/Kubernetes deployments, the outer container's device bindings
and cgroup policy must enforce the assigned GPU. Check that inner containers
cannot use other GPUs. Do not equate `egl0` with host GPU 0, the first
CUDA-visible device, or the scheduler's GPU UUID. Enumerate devices inside the
actual application environment and record the mapping.

The first prototype should compare launching VirtualGL inside the SIF with
binding a compatible VirtualGL installation into it and invoking `vglrun`
there. The interposer libraries must be available in the application process,
along with the injected driver libraries and its X display authorization.
An outer `vglrun apptainer ...` command alone is not evidence that the inner
application received usable preload paths. Check the real launcher, then
choose the smallest compatible solution.

The inspected [Neurocommand runtime adapter](https://github.com/neurodesk/neurocommand/blob/81f85ca479a270ca311fbb5f4b320d16e8787edb/neurodesk/transparent-singularity/container_runtime.sh)
adds `--cleanenv` for `exec` and explicitly restores `DISPLAY` and applicable
`XAUTHORITY` settings. Consequently, an outer VirtualGL preload environment
does not simply propagate to the inner app. Invoke `vglrun` inside the tool
container and provide its compatible libraries there. This finding describes
the inspected upstream revision; confirm the deployed image's pinned
Neurocommand revision before designing its integration.

After that experiment, add a launcher integration in Neurocommand that covers
both terminal and desktop-menu execution for one application, initially
FreeView. A GPU launch failure should report the selected device and safe
error context. An automatic software fallback must happen before launching
the application, not by repeating a failed application command that may have
already opened a window or changed files. Keep explicit software opt-out.

## Alternatives and security constraints

| Approach | Role in this proposal |
| --- | --- |
| VirtualGL EGL with existing VNC | First NVIDIA experiment; selected applications render on GPU while desktop transport stays in place |
| VirtualGL GLX with a GPU Xorg server | Compatibility fallback if EGL cannot support a required app; adds GPU X-server lifecycle and shared X authorization |
| TurboVNC | Later encoding/transport experiment if measured streaming performance is insufficient |
| Kasm VirtualGL or DRI3 | Separate variant and acceptance work, not a prerequisite for the Jupyter desktop |

The GLX route grants applications trusted access to a GPU X server, which
can expose that server's input and windows to other authorized clients. EGL
avoids that shared 3D X-server dependency but still requires controlled device
access. See sections 6.2 and 6.3 of the
[VirtualGL guide](https://raw.githubusercontent.com/VirtualGL/virtualgl/3.1.5/doc/index.html).
Keep per-user X authorization, loopback desktop services, and authenticated
Jupyter proxy access. GPU support should not require broadening the base
image's package-only sudo policy or using `xhost +`. Nested-container/CVMFS
privileges need their own existing deployment contract.

Our [Kasm image](../architecture/kasm.md) copies KasmVNC and startup services
from its donor, but does not copy the donor's complete graphics dependency
stack. Its [workspace metadata](../../config/kasm/publishing/workspace.json)
currently requests zero GPUs. A GPU derivative must audit VirtualGL libraries,
device selection, and startup integration rather than assume KasmVNC alone
provides NVIDIA acceleration. Kasm documents NVIDIA runtime setup and GPU
assignment in its [GPU guide](https://www.kasmweb.com/docs/latest/how_to/gpu.html),
and advises against sharing GPUs across security boundaries.

KasmVNC also has a native DRI3 path for compatible open-source Intel/AMD
drivers. Its [acceleration guide](https://kasmweb.com/kasmvnc/docs/master/gpu_acceleration.html)
does not provide that path for the proprietary NVIDIA driver. This is a
separate AMD/Intel investigation. The retrieved Kasm `latest` Workspaces guide
identifies itself as 1.17.0; do not treat it as proof of compatibility with our
pinned 1.19.0 runtime.

## Proposed acceptance gates

Follow [testing](../testing.md) when implementation starts. Put device-free
launcher behavior tests in `tests/unit/` and installed GPU execution tests in
`tests/container/`. Existing desktop application tests deliberately request
software rendering, so passing them alone does not validate GPU rendering.

1. Build a pinned VirtualGL candidate. Version 3.1.5 has
   [amd64 and arm64 Debian assets](https://github.com/VirtualGL/virtualgl/releases/tag/3.1.5),
   but package availability does not establish arm64 runtime support. Start
   with amd64 and preserve existing no-GPU arm64 coverage.
2. As the notebook user, run `/opt/VirtualGL/bin/eglinfo egl0`, then
   `/opt/VirtualGL/bin/vglrun -d egl0 -c proxy glxinfo -B` inside the actual VNC display. Record the
   graphics vendor, renderer, device mapping, host driver, runtime version, and
   image digest. Require the assigned hardware renderer, not `llvmpipe`.
3. Open a representative FreeView volume/surface through the real Neurocommand
   launcher inside Apptainer. Exercise both terminal and LXDE menu launches,
   rotate the surface in the browser, and confirm hardware rendering from the
   application process. Test FSLeyes, MRView, ITK-SNAP, and Slicer separately
   before advertising support for each.
4. Measure the same scene, viewport, and network conditions with software and
   GPU rendering. Record frame rate, input latency, CPU load, GPU load/memory,
   and streaming bandwidth. Do not promise a speedup from a renderer string.
5. Cover no GPU, compute-only libraries, failed EGL initialization, explicit
   opt-out, multiple devices, older SIF libc, display restart, and simultaneous
   VNC/RDP sessions. Keep TigerVNC's Mesa child restriction and software desktop
   startup working with NVIDIA libraries injected.
6. On a worker with at least two GPUs, allocate one and verify both outer and
   inner applications cannot open the other. Test two users' desktop/display
   isolation. Reserve GPU sharing for a separately defined trust/resource
   policy; allocation alone does not guarantee graphics memory isolation.
7. Run the full unit suite and relevant desktop/security container checks.
   Validate RDP separately before making the feature backend-independent. Kasm
   changes also require `scripts/verify_kasm_image.sh` plus the actual Kasm
   platform GPU session test.

The implementation decision depends on the nested FreeView experiment and
measured browser interaction. Until those pass, GPU-backed desktop sessions
remain a proposal rather than a supported deployment configuration.
