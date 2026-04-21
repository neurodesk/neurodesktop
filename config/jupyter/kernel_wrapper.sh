#!/bin/bash
# Wrapper for Jupyter kernel processes (python3, bash, etc.). Sources the
# Neurodesk environment so MODULEPATH reflects the current CVMFS state at the
# moment the kernel spawns - not at the time the Jupyter server started.
#
# This matters in lazy CVMFS mode (the default): the Jupyter server is
# launched before CVMFS is mounted, so its inherited MODULEPATH only contains
# local containers. Wrapping the kernel lets each new kernel pick up the
# CVMFS MODULEPATH once the deferred worker has mounted it - mirroring what
# a freshly opened terminal gets via /etc/bash.bashrc.
source /opt/neurodesktop/environment_variables.sh >/dev/null 2>&1
exec "$@"
