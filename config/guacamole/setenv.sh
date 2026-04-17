#!/usr/bin/env bash
export JAVA_OPTS="-Xms512M -Xmx1024M"
export CATALINA_OPTS="-Xms512M -Xmx1024M"
# -XX:UseSVE=0 only exists on aarch64 so only add it if it exists. Check using `uname -m`
if [ "$(uname -m)" == "aarch64" ]; then
  export JAVA_OPTS="$JAVA_OPTS -XX:UseSVE=0"
  export CATALINA_OPTS="$CATALINA_OPTS -XX:UseSVE=0"
fi
# GUACAMOLE_HOME points at a user-writable directory so that per-user ports,
# VNC passwords, and the Guacamole web credential can be stamped into the live
# user-mapping.xml at container start. Under Apptainer the container rootfs
# (including /etc/guacamole) is read-only, so a writable location is required
# to avoid silent cross-user session leaks on shared compute nodes. The Tomcat
# webapp walks GUACAMOLE_HOME first and falls back to /etc/guacamole, so the
# build-time templates under /etc/guacamole still act as the defaults.
HOME_DIR="${HOME:-/home/jovyan}"
export GUACAMOLE_HOME="${GUACAMOLE_HOME:-${HOME_DIR}/.neurodesk/guacamole}"
mkdir -p "${GUACAMOLE_HOME}" 2>/dev/null || true

# Let guacamole.sh override the Tomcat HTTP port per user. Default remains 8080
# so a bare `startup.sh` (tests, local dev) continues to work. server.xml uses
# `${port.http}` with this value substituted in.
export CATALINA_OPTS="${CATALINA_OPTS} -Dport.http=${NEURODESKTOP_TOMCAT_PORT:-8080}"