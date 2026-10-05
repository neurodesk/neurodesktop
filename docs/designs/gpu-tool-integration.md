---
title: GPU tool integration checklist
description: Required FreeSurfer image and Neurocommand changes for VirtualGL
  across the clean Apptainer execution boundary
parent: index.md
status: proposed
last-reviewed: "2026-10-05"
---

# GPU tool integration checklist

Neurodesktop ships the [desktop-side prerequisites](../architecture/desktop.md#gpu-rendering-prerequisites).
The following changes belong in Neurocontainers and Neurocommand and are not
implemented by this repository. Keep `--cleanenv` and initialize VirtualGL
inside the scientific container. Forward a small set of rendering controls;
host `LD_PRELOAD` and `LD_LIBRARY_PATH` do not belong in the shared environment.

The checklist uses the inspected
[FreeSurfer recipe](https://github.com/neurodesk/neurocontainers/blob/6dda2d2b66d4393dc8e8f32fc51b25baf103720d/recipes/freesurfer/build.yaml),
[Neurocommand runtime](https://github.com/neurodesk/neurocommand/blob/81f85ca479a270ca311fbb5f4b320d16e8787edb/neurodesk/transparent-singularity/container_runtime.sh),
and [artifact renderer](https://github.com/neurodesk/neurocommand/blob/81f85ca479a270ca311fbb5f4b320d16e8787edb/neurodesk/transparent-singularity/artifact_renderer.py).
Recheck those contracts when implementing the changes. `NEURODESK_VIRTUALGL`
below is a proposed control, not a currently supported setting.

## Neurocontainers FreeSurfer image

1. Update `recipes/freesurfer/build.yaml`, the current Ubuntu 22.04/amd64
   FreeSurfer 8.2.0 recipe. Install checksum-pinned VirtualGL 3.1.5 in that
   image, including its interposer libraries and EGL diagnostic. Use the
   amd64 asset/checksum in Neurodesktop's Dockerfile and verify compatibility
   with this older userspace. Install its package dependencies, notably
   `libxtst6`, `libxv1`, `libglu1-mesa`, and `libegl1`. NVIDIA driver libraries
   must come from the runtime; do not install a fixed NVIDIA host driver in
   the recipe. Keep the existing FreeSurfer/Qt fixes and license handling.
2. Add opt-in rendering to the existing `freeview_bin` wrapper after it locates
   `FREEVIEW_BIN_REAL` and filters conflicting MCR library directories. With
   `NEURODESK_VIRTUALGL=1`, execute VirtualGL there, in the final application
   environment. Unset/`0` retains the existing launch path; reject other values.
   Invoke it once and return its exit status. Do not retry an application
   through another renderer after it may have opened files or a window.

   The final launch shape is:

   ```bash
   LD_LIBRARY_PATH="$RESOLVED_LD" exec /opt/VirtualGL/bin/vglrun \
     -d "${VGL_DISPLAY:-egl0}" -c proxy "$FREEVIEW_BIN_REAL" "$@"
   ```

3. Fix driver-library precedence in that wrapper. It currently prepends bundled
   Qt and system library directories before appending the incoming
   `LD_LIBRARY_PATH`. Preserve the injected GPU driver directory, normally
   `/.singularity.d/libs`, ahead of distribution GL/EGL libraries while retaining
   the Qt/MCR exclusions. Confirm which driver libraries the actual `--nv`
   configuration binds; EGL vendor manifests and their referenced driver
   libraries must also be usable inside the SIF. Check the NVIDIA GLVND
   manifest, normally `/usr/share/glvnd/egl_vendor.d/10_nvidia.json`, and its
   `libEGL_nvidia.so.0` dependency. Preserve VirtualGL's
   application-local preload through the final `exec`.
4. Keep `deploy.bins: [recon-all, freeview]` and `freeviewGUI -> freeview` routing
   through the existing wrapper chain. Terminal and menu launches then share
   the same opt-in path without aliases or exposing host-side `vglrun` wrappers
   as FreeSurfer commands. If `tkmeditfv` is offered, implement and validate its
   own inner launch path before advertising it.
5. Rebuild and publish a new dated image/SIF and its deployment inventory.
   Existing CVMFS SIFs are immutable and will not acquire VirtualGL from a
   Neurodesktop update. Add FreeSurfer tests for default/off launches, enabled
   launch argument preservation, MCR filtering, GPU driver precedence, and
   failure without relaunch. On a GPU worker, initialize EGL inside that SIF,
   identify the assigned NVIDIA renderer, and open/rotate a real FreeView scene.

## Neurocommand runtime and generated artifacts

1. Keep the `neurodesk_container exec` `--cleanenv` option. Add a validated
   `NEURODESK_VIRTUALGL=0|1` control and explicitly supply it through Apptainer
   or Singularity `--env` when invoking supported tool containers. When enabled,
   also supply `VGL_DISPLAY`, defaulting to `egl0`. The argument-array shape is:

   ```bash
   options+=(--env "NEURODESK_VIRTUALGL=1" --env "VGL_DISPLAY=${VGL_DISPLAY:-egl0}")
   ```

   Passing a rendering device alone does not enable acceleration. Also pass an
   explicit `0` so an image default or inherited injected setting cannot undo
   the user's opt-out. Account for `APPTAINERENV_*`, `SINGULARITYENV_*`, legacy
   environment mode, and user-provided `--env` options when defining precedence;
   conflicting settings must not silently defeat the validated control.
2. When enabled, require the existing NVIDIA device/library binding path.
   Respect `NEURODESK_GPU=off` and explicit NVIDIA-bind opt-outs. Report an
   incompatible request rather than silently overriding them. Keep graphics
   device assignment controlled by the outer runtime/cgroup policy. A CUDA
   visibility variable does not select or isolate the EGL device.
3. Retain explicit `DISPLAY` and the existing read-only `XAUTHORITY` bind/env
   handling. Retain the X socket access already used by scientific GUIs. Bind
   the outer NVIDIA EGL vendor manifest read-only at its GLVND location
   for enabled launches if the selected runtime does not already provide it;
   require its referenced library to resolve inside the tool container. Do
   not forward the desktop's Mesa-only X-server vendor restriction, host
   loader paths, or arbitrary `VGL_*` variables across the boundary. The inner
   FreeSurfer wrapper owns `vglrun`, its preload paths, and `-c proxy`.
4. Record VirtualGL capability in the rebuilt tool's deployment metadata and
   check it before accepting an enabled launch. Older images and unsupported
   commands should report an unsupported request, not claim acceleration.
   If this needs a new inventory field, update `artifact_renderer.py`'s
   `ContainerSpec`/inventory reader and wrapper generation together, plus the
   producer in Neurocontainers. Existing `commands.txt` alone does not describe
   renderer support. Keep generated wrappers relocatable and preserve manual
   wrapper protection during reconciliation.
5. Regenerate/reconcile the new FreeSurfer deployment wrappers and menu/module
   artifacts. The generated wrapper can continue calling
   `neurodesk_container exec IMAGE freeview "$@"`; the inner FreeSurfer wrapper
   performs rendering. Confirm LXDE starts with the configured controls so a
   setting in a terminal alone is not mistaken for a menu-wide setting.
6. Test the runtime with a capturing stub to prove `--cleanenv`, the narrow
   `--env` values, display authorization, NVIDIA binds, quoting, and opt-outs.
   Cover both Apptainer and Singularity, default/off/enabled cases, legacy mode,
   unsupported older SIFs, and conflicting injected/custom settings. Then test
   the complete terminal and menu FreeView paths with the new SIF on hardware.

The resulting process order is:

```text
Neurocommand freeview wrapper
  -> Apptainer exec --cleanenv --nv with explicit rendering/display settings
  -> image freeview -> image freeview_bin
  -> image vglrun -> freeview_bin_real
```

GPU worker deployment must also request NVIDIA graphics capabilities, allocate
the intended GPU, and verify device isolation. See the
[original assessment](gpu-desktop-sessions.md) for deployment
examples and the hardware acceptance gates. Installing the desktop package
alone does not complete this cross-repository launch path.
