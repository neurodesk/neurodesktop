###############################################################################
# Stage 1: Builder — compile Guacamole server, patch code-server, install codex
###############################################################################
FROM quay.io/jupyter/base-notebook:2026-03-23 AS builder
# https://quay.io/repository/jupyter/base-notebook?tab=tags

USER root

ARG BUILD_ONLY_APT_PACKAGES="build-essential libcairo2-dev libjpeg-turbo8-dev libpng-dev libtool-bin freerdp2-dev libvncserver-dev libssl-dev libwebp-dev libssh2-1-dev libpango1.0-dev"
ARG GUACAMOLE_VERSION="1.6.0"
ARG CODE_SERVER_VERSION="4.104.2"

# Install build dependencies + nodejs for npm operations
RUN apt-get update --yes \
    && DEBIAN_FRONTEND=noninteractive apt install --yes --no-install-recommends \
    ${BUILD_ONLY_APT_PACKAGES} \
    wget \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install --yes --no-install-recommends nodejs yarn \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Build Guacamole server
RUN wget -q "https://archive.apache.org/dist/guacamole/${GUACAMOLE_VERSION}/source/guacamole-server-${GUACAMOLE_VERSION}.tar.gz" -P /tmp \
    && tar xvf /tmp/guacamole-server-${GUACAMOLE_VERSION}.tar.gz -C /tmp \
    && rm /tmp/guacamole-server-${GUACAMOLE_VERSION}.tar.gz \
    && cd /tmp/guacamole-server-${GUACAMOLE_VERSION} \
    && ./configure --with-init-dir=/etc/init.d \
    && make \
    && make install \
    && ldconfig \
    && rm -r /tmp/guacamole-server-${GUACAMOLE_VERSION}

# Install code-server as a prebuilt binary and patch basic-ftp
RUN set -eux; \
    deb_arch="$(dpkg --print-architecture || true)"; \
    kernel_arch="$(uname -m || true)"; \
    cs_arch=""; \
    for arch in "${deb_arch}" "${kernel_arch}"; do \
    case "${arch}" in \
    amd64|x86_64) cs_arch="amd64" ;; \
    arm64|aarch64) cs_arch="arm64" ;; \
    armhf|armv7l|armv7) cs_arch="armv7l" ;; \
    esac; \
    if [ -n "${cs_arch}" ]; then break; fi; \
    done; \
    if [ -z "${cs_arch}" ]; then \
    echo "Unsupported architecture for code-server: deb_arch=${deb_arch} kernel_arch=${kernel_arch}" >&2; \
    exit 1; \
    fi; \
    cs_tar="code-server-${CODE_SERVER_VERSION}-linux-${cs_arch}.tar.gz"; \
    curl -fL --retry 5 --retry-all-errors --retry-delay 2 \
    "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/${cs_tar}" \
    -o "/tmp/${cs_tar}"; \
    tar -xzf "/tmp/${cs_tar}" -C /tmp; \
    rm -rf /opt/code-server; \
    mv "/tmp/code-server-${CODE_SERVER_VERSION}-linux-${cs_arch}" /opt/code-server; \
    # code-server currently ships basic-ftp@5.0.5 in npm-shrinkwrap.json; update to the patched release.
    cd /opt/code-server; \
    npm update --no-audit --no-fund basic-ftp; \
    rm -f "/tmp/${cs_tar}"; \
    npm cache clean --force



###############################################################################
# Stage 2: Final runtime image
###############################################################################
FROM quay.io/jupyter/base-notebook:2026-03-23
# https://quay.io/repository/jupyter/base-notebook?tab=tags

LABEL maintainer="Neurodesk Project <www.neurodesk.org>"

USER root

ARG GUACAMOLE_RUNTIME_APT_PACKAGES="libfreerdp2-2t64 libfreerdp-client2-2t64 libwinpr2-2t64 libvncclient1"

#========================================#
# Core services
#========================================#


# Install base image dependencies (runtime only — build deps are in the builder stage)
RUN apt-get update --yes \
    && DEBIAN_FRONTEND=noninteractive apt install --yes --no-install-recommends \
    software-properties-common \
    openjdk-21-jre-headless \
    ${GUACAMOLE_RUNTIME_APT_PACKAGES} \
    tigervnc-common \
    tigervnc-standalone-server \
    tigervnc-tools \
    xorgxrdp \
    xrdp \
    lxde \
    dbus-x11 \
    acl \
    wget \
    curl \
    dirmngr \
    gpg \
    gpg-agent \
    apt-transport-https \
    xz-utils \
    && usermod -a -G ssl-cert xrdp \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy Guacamole server binaries and libraries from builder.
# Use tar round-trip to preserve symlinks (COPY dereferences them, tripling
# disk usage and producing ldconfig warnings).
COPY --from=builder /usr/local/sbin/guacd /usr/local/sbin/guacd
COPY --from=builder /etc/init.d/guacd /etc/init.d/guacd
RUN --mount=type=bind,from=builder,source=/usr/local/lib,target=/tmp/builder-lib \
    cd /tmp/builder-lib && tar cf - libguac* | tar xf - -C /usr/local/lib/ \
    && ldconfig

# Copy code-server from builder (already patched)
COPY --from=builder /opt/code-server /opt/code-server
RUN ln -sf /opt/code-server/bin/code-server /usr/local/bin/code-server

# add a static strace executable to /opt which we can copy to containers for debugging:
RUN mkdir -p /opt/strace \
    && wget -qO- https://github.com/JuliaBinaryWrappers/strace_jll.jl/releases/download/strace-v6.7.0%2B1/strace.v6.7.0.x86_64-linux-gnu.tar.gz | tar xz -C /opt/strace --strip-components=1 \
    && chmod +x /opt/strace

ARG TOMCAT_REL="9"
ARG TOMCAT_VERSION="9.0.116"
ARG GUACAMOLE_VERSION="1.6.0"

ENV LANG=""
ENV LANGUAGE=""
ENV LC_ALL=""

# Install apptainer
RUN add-apt-repository -y ppa:apptainer/ppa \
    && apt-get update --yes \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes apptainer \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && rm -rf /root/.cache && rm -rf /home/${NB_USER}/.cache

# Install Apache Tomcat
RUN wget -q https://archive.apache.org/dist/tomcat/tomcat-${TOMCAT_REL}/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz -P /tmp \
    && tar -xf /tmp/apache-tomcat-${TOMCAT_VERSION}.tar.gz -C /tmp \
    && rm -rf /tmp/apache-tomcat-${TOMCAT_VERSION}.tar.gz \
    && mv /tmp/apache-tomcat-${TOMCAT_VERSION} /usr/local/tomcat \
    && mv /usr/local/tomcat/webapps /usr/local/tomcat/webapps.dist \
    && mkdir /usr/local/tomcat/webapps \
    && if ! grep -q 'maxHttpRequestHeaderSize=' /usr/local/tomcat/conf/server.xml; then \
        sed -i '/<Connector port="8080" protocol="HTTP\/1\.1"/,/^[[:space:]]*\/>/ s|^[[:space:]]*\/>$|               maxHttpRequestHeaderSize="65536"\
               />|' /usr/local/tomcat/conf/server.xml; \
    fi \
    # Make the Connector port settable per-user via CATALINA_OPTS=-Dport.http=NNNN.
    # Needed under Apptainer where multiple users share the host netns and cannot
    # all bind 8080. catalina.properties supplies 8080 as the fallback so running
    # Tomcat without setenv.sh (dev / tests) still works.
    && sed -i 's|<Connector port="8080"|<Connector port="${port.http}"|' /usr/local/tomcat/conf/server.xml \
    && grep -q 'port.http' /usr/local/tomcat/conf/server.xml \
    && echo "port.http=8080" >> /usr/local/tomcat/conf/catalina.properties \
    # Prevent cookie accumulation (Safari "Request header too large" fix):
    # 1. Set sessionCookiePath="/" so all cookies share one path (no duplicates per sub-path)
    # 2. Add Rfc6265CookieProcessor with SameSite=Lax (Strict breaks proxied access in Safari)
    && sed -i 's|<Context>|<Context sessionCookiePath="/">|' /usr/local/tomcat/conf/context.xml \
    && sed -i '/<Context sessionCookiePath/a\    <CookieProcessor className="org.apache.tomcat.util.http.Rfc6265CookieProcessor" sameSiteCookies="Lax" />' /usr/local/tomcat/conf/context.xml \
    # 3. Set Max-Age on session cookie so browsers auto-expire it (24h) in default web.xml
    && sed -i '/<session-config>/,/<\/session-config>/c\    <session-config>\n        <session-timeout>30</session-timeout>\n        <cookie-config>\n            <max-age>86400</max-age>\n            <http-only>true</http-only>\n        </cookie-config>\n    </session-config>' /usr/local/tomcat/conf/web.xml \
    && chmod +x /usr/local/tomcat/bin/*.sh

# Install Apache Guacamole WAR
RUN wget -q "https://archive.apache.org/dist/guacamole/${GUACAMOLE_VERSION}/binary/guacamole-${GUACAMOLE_VERSION}.war" -O /usr/local/tomcat/webapps/ROOT.war

# #========================================#
# # Software (as root user)
# #========================================#

# Workaround for CVMFS to break systemctl by replacing it with a dummy script
RUN mv /usr/bin/systemctl /usr/bin/systemctl.orig \
    && echo '#!/bin/bash' > /usr/bin/systemctl \
    && echo 'echo "systemctl is disabled in this container"' >> /usr/bin/systemctl \
    && chmod +x /usr/bin/systemctl

# Install CVMFS
RUN wget -q https://cvmrepo.s3.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb -P /tmp \
    && dpkg -i /tmp/cvmfs-release-latest_all.deb \
    && rm /tmp/cvmfs-release-latest_all.deb \
    && apt-get update --yes \
    && DEBIAN_FRONTEND=noninteractive apt install --yes --no-install-recommends \
    autofs \
    cvmfs \
    uuid-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Tools and Libs
RUN apt-get update --yes \
    && DEBIAN_FRONTEND=noninteractive apt install --yes --no-install-recommends \
    aria2 \
    bc \
    davfs2 \
    dnsutils \
    gedit \
    gh \
    git \
    git-annex \
    gnome-keyring \
    graphviz \
    htop \
    imagemagick \
    iputils-ping \
    less \
    libgfortran5 \
    libgpgme-dev \
    libossp-uuid-dev \
    libpci3 \
    libreoffice-core \
    lmod \
    lua-bit32 \
    lua-filesystem \
    lua-json \
    lua-lpeg \
    lua-posix \
    lua-term \
    lua5.2 \
    lxtask \
    man-db \
    nano \
    nextcloud-desktop \
    openssh-client \
    openssh-server \
    owncloud-client \
    pciutils \
    python3-setuptools \
    qdirstat \
    rsync \
    rclone \
    s3fs \
    screen \
    slurm-client \
    slurm-wlm-basic-plugins \
    slurmctld \
    slurmd \
    slurmdbd \
    mariadb-server \
    sshfs \
    munge \
    tcllib \
    tk \
    tmux \
    tree \
    uidmap \
    unzip \
    vim \
    xdg-utils \
    zip \
    tcsh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install a targeted TinyTeX toolchain for notebook PDF export without pulling
# in the larger Debian TeX Live package set.
RUN HOME=/root /bin/bash -lc 'set -euo pipefail; \
    mkdir -p /root/.local/bin; \
    curl -fsSL https://yihui.org/tinytex/install-bin-unix.sh | sh; \
    mv /root/.TinyTeX /opt/TinyTeX; \
    tlmgr_path="$(echo /opt/TinyTeX/bin/*/tlmgr)"; \
    "${tlmgr_path}" option sys_bin /usr/local/bin >/dev/null; \
    "${tlmgr_path}" path add >/dev/null; \
    "${tlmgr_path}" install \
    tcolorbox \
    parskip \
    caption \
    float \
    geometry \
    amsmath \
    amsfonts \
    latex \
    upquote \
    eurosym \
    fontspec \
    unicode-math \
    fancyvrb \
    grffile \
    adjustbox \
    hyperref \
    titling \
    tools \
    booktabs \
    enumitem \
    ulem \
    soul \
    jknapltx \
    rsfs \
    graphics \
    iftex \
    xcolor \
    pdfcol; \
    rm -rf /root/.cache /root/.local'

# Extract Guacamole WAR and patch its web.xml to set session cookie Max-Age.
# Guacamole's own WEB-INF/web.xml overrides Tomcat's conf/web.xml, so without
# this patch cookies have no expiry and Safari accumulates them until headers
# exceed the size limit ("Request header is too large").
RUN unzip -q /usr/local/tomcat/webapps/ROOT.war -d /usr/local/tomcat/webapps/ROOT \
    && rm /usr/local/tomcat/webapps/ROOT.war \
    && if grep -q '<session-config>' /usr/local/tomcat/webapps/ROOT/WEB-INF/web.xml; then \
        sed -i '/<session-config>/,/<\/session-config>/c\    <session-config>\n        <session-timeout>30</session-timeout>\n        <cookie-config>\n            <max-age>86400</max-age>\n            <http-only>true</http-only>\n        </cookie-config>\n    </session-config>' /usr/local/tomcat/webapps/ROOT/WEB-INF/web.xml; \
    else \
        sed -i 's|</web-app>|    <session-config>\n        <session-timeout>30</session-timeout>\n        <cookie-config>\n            <max-age>86400</max-age>\n            <http-only>true</http-only>\n        </cookie-config>\n    </session-config>\n</web-app>|' /usr/local/tomcat/webapps/ROOT/WEB-INF/web.xml; \
    fi

# Install Nextflow ecosystem tools
ENV NF_NEURO_MODULES_DIR=/opt/nf-neuro/modules
ENV NF_TEST_HOME=/opt/nf-test
RUN mkdir -p "${NF_TEST_HOME}" \
    && cd /tmp \
    && curl -fsSL https://get.nextflow.io | bash \
    && mv /tmp/nextflow /usr/local/bin/nextflow \
    && chmod 755 /usr/local/bin/nextflow \
    && wget -qO- https://get.nf-test.com | bash -s 0.9.5 \
    && test -f "${HOME}/.nf-test/nf-test.jar" \
    && cp -a "${HOME}/.nf-test/." "${NF_TEST_HOME}/" \
    && printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'exec java -jar /opt/nf-test/nf-test.jar "$@"' > /usr/local/bin/nf-test \
    && chmod 755 /usr/local/bin/nf-test \
    && mkdir -p /opt/nf-neuro \
    && git clone --depth=1 https://github.com/nf-neuro/modules.git "${NF_NEURO_MODULES_DIR}" \
    && chown -R ${NB_UID}:${NB_GID} /opt/nf-neuro "${NF_TEST_HOME}" "${HOME}/.nextflow" \
    && rm -rf /root/.cache "${HOME}/.nf-test" /tmp/nf-test /tmp/nextflow

# Install build tools temporarily — nodejs is needed by codex CLI at runtime and
# by hatch-jupyter-builder to compile JupyterLab extensions from source (e.g.
# jupyterlab-slurm, neurodesk-launcher). build-essential provides gcc for pip
# packages with C extensions (e.g. psutil, traits). build-essential is removed
# after extensions are built (see purge step below); nodejs stays for codex.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install --yes --no-install-recommends nodejs build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install AI coding assistants
RUN npm install -g @openai/codex \
    && rm -rf /root/.npm \
    && su - "${NB_USER}" -c 'curl -fsSL https://claude.ai/install.sh | bash -s -- stable' \
    && mkdir -p /opt/jovyan_defaults/.local/bin \
    && if [ -x /home/jovyan/.local/bin/claude ]; then \
    cp -L /home/jovyan/.local/bin/claude /opt/jovyan_defaults/.local/bin/claude; \
    else \
    cp -L /home/jovyan/.local/share/claude/versions/* /opt/jovyan_defaults/.local/bin/claude; \
    fi \
    && chmod +x /opt/jovyan_defaults/.local/bin/claude \
    && rm -rf /home/${NB_USER}/.cache \
    && rm -rf /home/${NB_USER}/.local

# Install OpenCode CLI (open source AI coding agent)
RUN curl -fsSL https://opencode.ai/install | bash \
    && mv /home/jovyan/.opencode/bin/opencode /usr/bin/opencode \
    && rm -rf /home/${NB_USER}/.cache /home/${NB_USER}/.local

# Install firefox
RUN add-apt-repository ppa:mozillateam/ppa \
    && apt-get update --yes \
    && DEBIAN_FRONTEND=noninteractive apt install --yes --no-install-recommends \
    --target-release 'o=LP-PPA-mozillateam' firefox \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && rm -rf /home/${NB_USER}/.cache /home/${NB_USER}/.local
COPY config/firefox/mozillateamppa /etc/apt/preferences.d/mozillateamppa
COPY config/firefox/syspref.js /etc/firefox/syspref.js

#========================================#
# Software (as notebook user)
#========================================#

USER ${NB_USER}

# Install conda packages
RUN conda install -c conda-forge nb_conda_kernels \
    && conda clean --all -f -y \
    && rm -rf /home/${NB_USER}/.cache
RUN conda config --system --prepend envs_dirs '~/conda-environments'

# Install Python packages and JupyterLab extensions
ARG BUST_CACHE_PIP=2
RUN /opt/conda/bin/pip install \
    datalad \
    nipype \
    nbdev \
    nf-core \
    snakemake \
    pydra==1.0a7 \
    nipoppy \
    matplotlib \
    datalad-container \
    datalad-osf \
    osfclient \
    watermark \
    ipyniivue \
    jupyter-server-proxy \
    jupyterlmod \
    jupyterlab-git \
    notebook_intelligence \
    jupyterlab_rise \
    jupyterlab-niivue==0.2.5 \
    jupyterlab_myst \
    jupyter-sshd-proxy \
    papermill \
    ipycanvas \
    jupyter-resource-usage \
    jupyter_scheduler \
    jupyterlab-slurm@git+https://github.com/NERSC/jupyterlab-slurm.git@main \
    httpx \
    ipywidgets==7.8.5 \
    ipyvolume \
    jupyterlab_widgets \
    nbgitpuller \
    xnat \
    pytest \
    "requests>=2.32.3" \
    "chardet<6" \
    && /opt/conda/bin/jupyter labextension disable @jupyterlab/apputils-extension:announcements \
    && rm -rf /home/${NB_USER}/.cache

# Build and install neurodesk-launcher JupyterLab extension
COPY --chown=${NB_USER}:users extensions/neurodesk-launcher /tmp/neurodesk-launcher
RUN cd /tmp/neurodesk-launcher \
    && /opt/conda/bin/pip install . \
    && rm -rf /tmp/neurodesk-launcher \
    && /opt/conda/bin/jupyter labextension disable @jupyterhub/jupyter-server-proxy \
    && rm -rf /home/${NB_USER}/.cache

#========================================#
# Configuration (as root user)
#========================================#

USER root

# Remove build-time packages: -dev headers for pip native extensions and
# build-essential for C extensions. nodejs stays — codex CLI needs it at runtime.
# (Guacamole build deps are already excluded via multi-stage build.)
RUN DEBIAN_FRONTEND=noninteractive apt-get purge --yes --auto-remove \
    libgpgme-dev libossp-uuid-dev build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# # Customise logo, wallpaper, terminal
COPY config/jupyter/neurodesk_brain_logo.svg /opt/neurodesk_brain_logo.svg
COPY config/jupyter/neurodesk_brain_icon.svg /opt/neurodesk_brain_icon.svg
COPY config/jupyter/vscode_logo.svg /opt/vscode_logo.svg

COPY config/lxde/background.png /usr/share/lxde/wallpapers/desktop_wallpaper.png
COPY config/lxde/pcmanfm.conf /etc/xdg/pcmanfm/LXDE/pcmanfm.conf
COPY config/lxde/lxterminal.conf /usr/share/lxterminal/lxterminal.conf
COPY config/lmod/module.sh /usr/share/

# Configure tiling of windows SHIFT-ALT-CTR-{Left,right,top,Bottom} and other openbox desktop mods
COPY ./config/lxde/rc.xml /etc/xdg/openbox

# Allow root user to access sshfs mount, fix "No session for pid prompt", enable rootless mounts
RUN sed -i 's/#user_allow_other/user_allow_other/g' /etc/fuse.conf \
    && rm /usr/bin/lxpolkit \
    && chmod +x /usr/bin/fusermount

# Add notebook startup scripts
# https://jupyter-docker-stacks.readthedocs.io/en/latest/using/common.html
RUN mkdir -p /usr/local/bin/start-notebook.d/ \
    && mkdir -p /usr/local/bin/before-notebook.d/
COPY config/jupyter/start_notebook.sh /usr/local/bin/start-notebook.d/
COPY config/jupyter/before_notebook.sh /usr/local/bin/before-notebook.d/

# Add jupyter notebook and startup scripts for system-wide configuration
# Note: jupyter_notebook_config.py is generated from template + webapps.json below
COPY --chown=root:users config/jupyter/jupyterlab_startup.sh /opt/neurodesktop/jupyterlab_startup.sh
COPY --chown=root:users config/jupyter/deferred_startup.sh /opt/neurodesktop/deferred_startup.sh
COPY --chown=root:users config/guacamole/guacamole.sh /opt/neurodesktop/guacamole.sh
COPY --chown=root:users config/guacamole/init_secrets.sh /opt/neurodesktop/init_secrets.sh
COPY --chown=root:users config/guacamole/ensure_rdp_backend.sh /opt/neurodesktop/ensure_rdp_backend.sh
COPY --chown=root:users config/jupyter/environment_variables.sh /opt/neurodesktop/environment_variables.sh
COPY --chown=root:users config/jupyter/kernel_wrapper.sh /opt/neurodesktop/kernel_wrapper.sh
COPY --chown=root:users config/ssh/ensure_sftp_sshd.sh /opt/neurodesktop/ensure_sftp_sshd.sh
COPY --chown=root:users config/slurm/setup_and_start_slurm.sh /opt/neurodesktop/setup_and_start_slurm.sh
COPY --chown=root:users tests /opt/tests
# COPY --chown=root:users config/guacamole/user-mapping.xml /etc/guacamole/user-mapping.xml

# Generic webapp infrastructure
COPY --chown=root:users scripts/generate_jupyter_config.py /opt/neurodesktop/scripts/generate_jupyter_config.py
COPY --chown=root:users config/jupyter/webapp_wrapper /opt/neurodesktop/webapp_wrapper
COPY --chown=root:users config/jupyter/webapp_launcher.sh /opt/neurodesktop/webapp_launcher.sh
COPY --chown=root:users config/jupyter/jupyter_notebook_config.py.template /opt/neurodesktop/jupyter_notebook_config.py.template

# Fetch webapps.json from neurocommand and generate jupyter config
RUN curl -fsSL https://raw.githubusercontent.com/neurodesk/neurocommand/main/neurodesk/webapps.json \
    -o /opt/neurodesktop/webapps.json \
    && python3 /opt/neurodesktop/scripts/generate_jupyter_config.py \
    /opt/neurodesktop/webapps.json \
    /opt/neurodesktop/jupyter_notebook_config.py.template \
    /etc/jupyter/jupyter_notebook_config.py

RUN chmod +rx /etc/jupyter/jupyter_notebook_config.py \
    /opt/neurodesktop/jupyterlab_startup.sh \
    /opt/neurodesktop/deferred_startup.sh \
    /opt/neurodesktop/guacamole.sh \
    /opt/neurodesktop/init_secrets.sh \
    /opt/neurodesktop/ensure_rdp_backend.sh \
    /opt/neurodesktop/environment_variables.sh \
    /opt/neurodesktop/kernel_wrapper.sh \
    /opt/neurodesktop/ensure_sftp_sshd.sh \
    /opt/neurodesktop/setup_and_start_slurm.sh \
    /opt/neurodesktop/webapp_launcher.sh \
    /opt/neurodesktop/webapp_wrapper/webapp_wrapper.py \
    /opt/neurodesktop/scripts/generate_jupyter_config.py \
    && chmod +r /opt/neurodesktop/webapp_wrapper/splash_template.html \
    /opt/neurodesktop/webapps.json

# Create Guacamole configurations (user-mapping.xml gets filled in the startup.sh script)
RUN mkdir -p /etc/guacamole \
    && echo -e "user-mapping: /etc/guacamole/user-mapping.xml\nguacd-hostname: 127.0.0.1" > /etc/guacamole/guacamole.properties \
    && echo -e "[server]\nbind_host = 127.0.0.1\nbind_port = 4822" > /etc/guacamole/guacd.conf \
    && chown -R ${NB_UID}:${NB_GID} /etc/guacamole \
    && chown -R ${NB_UID}:${NB_GID} /usr/local/tomcat \
    # Apache Tomcat ships `conf/` as 0750 and a few script/dir modes that deny
    # world read/traverse. On Apptainer/HPC the container runs as an arbitrary
    # host UID with no membership in group `users`, so the chown above alone
    # is not enough - `cp -rfT /usr/local/tomcat/conf …` silently fails and
    # guacamole.sh cannot launch Tomcat. Grant world read + traverse so any
    # NB_UID can bootstrap its per-user CATALINA_BASE. Write access stays
    # restricted to the owner.
    && chmod -R a+rX /usr/local/tomcat /etc/guacamole
COPY --chown=${NB_UID}:${NB_GID} config/guacamole/user-mapping-vnc.xml /etc/guacamole/user-mapping-vnc.xml
COPY --chown=${NB_UID}:${NB_GID} config/guacamole/user-mapping-vnc-rdp.xml /etc/guacamole/user-mapping-vnc-rdp.xml
RUN ln -sf /etc/guacamole/user-mapping-vnc.xml /etc/guacamole/user-mapping.xml

# Configure NB_USER account defaults and JupyterLab settings
RUN /usr/bin/printf '%s\n%s\n' 'password' 'password' | passwd ${NB_USER} \
    && usermod --shell /bin/bash ${NB_USER} \
    && sed -i 's/c.FileContentsManager.delete_to_trash = False/c.FileContentsManager.always_delete_dir = True/g' /etc/jupyter/jupyter_server_config.py \
    && printf '\n# Detect dead WebSocket clients quickly (tab closed/network gone)\nc.ServerApp.websocket_ping_interval = 30\nc.ServerApp.websocket_ping_timeout = 60\n' >> /etc/jupyter/jupyter_server_config.py

# Patch every installed kernel spec so each kernel spawn re-sources
# /opt/neurodesktop/environment_variables.sh. This is how notebook kernels
# pick up the correct MODULEPATH (including lazily-mounted CVMFS) even though
# the parent Jupyter server was launched before CVMFS was ready. Covers
# python3, bash, R, Julia, etc. - any kernelspec with a kernel.json on disk.
# Idempotent: re-running leaves already-wrapped argv untouched.
RUN python3 <<'PY'
import json
from pathlib import Path

WRAPPER = "/opt/neurodesktop/kernel_wrapper.sh"
search_roots = [
    Path("/opt/conda/share/jupyter/kernels"),
    Path("/usr/local/share/jupyter/kernels"),
    Path("/usr/share/jupyter/kernels"),
]

for root in search_roots:
    if not root.is_dir():
        continue
    for spec_file in root.glob("*/kernel.json"):
        try:
            spec = json.loads(spec_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[WARN] Skipping {spec_file}: {exc}")
            continue
        argv = spec.get("argv")
        if not isinstance(argv, list) or not argv:
            continue
        if argv[0] == WRAPPER:
            continue
        spec["argv"] = [WRAPPER] + argv
        spec_file.write_text(json.dumps(spec, indent=1))
        print(f"[INFO] Wrapped kernel spec: {spec_file}")
PY

# Source environment variables in global bashrc for Apptainer/Singularity (which mounts host home)
# Also configure durable shell history globally so JupyterHub terminals get it even when
# notebook startup hooks are bypassed.
RUN cat >> /etc/bash.bashrc <<'EOF'
source /opt/neurodesktop/environment_variables.sh

# Neurodesk persistent bash history
if [[ $- == *i* ]]; then
    shopt -s histappend

    if [ -d "${HOME}/neurodesktop-storage" ] && [ -w "${HOME}/neurodesktop-storage" ]; then
        export HISTFILE="${HOME}/neurodesktop-storage/.bash_history"
    elif [ -d "/neurodesktop-storage" ] && [ -w "/neurodesktop-storage" ]; then
        export HISTFILE="/neurodesktop-storage/.bash_history"
    else
        export HISTFILE="${HISTFILE:-$HOME/.bash_history}"
    fi

    export HISTSIZE=100000
    export HISTFILESIZE=200000
    export HISTCONTROL=ignoredups:erasedups

    # Persist history continuously so abrupt terminal/session closes do not lose commands.
    if [[ "${PROMPT_COMMAND:-}" != *"history -a"* ]]; then
        if [ -n "${PROMPT_COMMAND:-}" ]; then
            export PROMPT_COMMAND="history -a; history -n; ${PROMPT_COMMAND}"
        else
            export PROMPT_COMMAND="history -a; history -n"
        fi
    fi
fi
EOF

#========================================#
# Home directory defaults (stored in /opt/jovyan_defaults/)
# Files are restored to user home on startup if they don't exist
#========================================#

ENV DONT_PROMPT_WSL_INSTALL=1
ENV LMOD_CMD=/usr/share/lmod/lmod/libexec/lmod

# Create defaults directory structure
RUN mkdir -p /opt/jovyan_defaults/.itksnap.org/ITK-SNAP \
    && mkdir -p /opt/jovyan_defaults/.config/lxpanel/LXDE/panels \
    && mkdir -p /opt/jovyan_defaults/.local/share/code-server/User \
    && mkdir -p /opt/jovyan_defaults/.config/libfm \
    && mkdir -p /opt/jovyan_defaults/.config/opencode \
    && mkdir -p /opt/jovyan_defaults/.local/bin \
    && mkdir -p /opt/jovyan_defaults/.vnc \
    && mkdir -p /opt/jovyan_defaults/.claude \
    && mkdir -p /opt/jovyan_defaults/.codex \
    && mkdir -p /opt/jovyan_defaults/.ssh \
    && mkdir -p /opt/jovyan_defaults/.jupyter/labconfig \
    && mkdir -p /opt/jovyan_defaults/.jupyter/nbi/rules

# Copy configuration files to defaults directory
COPY config/itksnap/UserPreferences.xml /opt/jovyan_defaults/.itksnap.org/ITK-SNAP/UserPreferences.xml
COPY config/lxde/mimeapps.list /opt/jovyan_defaults/.config/mimeapps.list
COPY config/lxde/panel /opt/jovyan_defaults/.config/lxpanel/LXDE/panels/panel
COPY config/vscode/settings.json /opt/jovyan_defaults/.local/share/code-server/User/settings.json
COPY config/lxde/libfm.conf /opt/jovyan_defaults/.config/libfm/libfm.conf
COPY config/lxde/xstartup /opt/jovyan_defaults/.vnc/xstartup
COPY config/conda/conda-readme.md /opt/jovyan_defaults/conda-readme.md
COPY config/agents/claude_settings.local.json /opt/jovyan_defaults/.claude/settings.local.json
COPY config/agents/claude_mcp_config.json /opt/jovyan_defaults/.claude/mcp_config.json
COPY config/agents/opencode_config.json /opt/jovyan_defaults/.config/opencode/opencode.json
COPY config/agents/codex_config.toml /opt/jovyan_defaults/.codex/config.toml
COPY config/ssh/sshd_config /opt/jovyan_defaults/.ssh/sshd_config
COPY config/jupyter/page_config.json /opt/jovyan_defaults/.jupyter/labconfig/page_config.json
COPY config/agents/AGENTS_nbi.md /opt/jovyan_defaults/.jupyter/nbi/rules/neurodesk.md
COPY config/agents/nbi_config.json /opt/jovyan_defaults/.jupyter/nbi/config.json
COPY --chown=root:users config/agents/nbi_setup.sh /opt/neurodesktop/nbi_setup.sh
RUN chmod +rx /opt/neurodesktop/nbi_setup.sh

# Special: bashrc content to append (not replace)
COPY config/lxde/.bashrc /opt/jovyan_defaults/.bashrc_append

# Generate VNC password at build time and store in defaults
RUN /usr/bin/printf '%s\n%s\n%s\n' 'password' 'password' 'n' | vncpasswd /opt/jovyan_defaults/.vnc/passwd

# Create marker files and set permissions
# Note: Don't chmod 700 .ssh here - it prevents access during restore
# The restore script sets proper permissions on the destination
RUN chmod +x /opt/jovyan_defaults/.vnc/xstartup \
    && chown root:users /opt/jovyan_defaults/.vnc/passwd \
    && chmod 640 /opt/jovyan_defaults/.vnc/passwd

# Copy restore script
COPY --chown=root:users config/jupyter/restore_home_defaults.sh /opt/neurodesktop/restore_home_defaults.sh
COPY --chown=root:users config/jupyter/update_page_config.py /opt/neurodesktop/update_page_config.py
RUN chmod +rx /opt/neurodesktop/restore_home_defaults.sh /opt/neurodesktop/update_page_config.py

# Add AGENTS.md to /opt for reference
COPY config/agents/AGENTS.md /opt/AGENTS.md

# Add AI agent wrapper scripts to /usr/local/sbin/
COPY --chown=root:root config/agents/claude /usr/local/sbin/claude
COPY --chown=root:root config/agents/opencode /usr/local/sbin/opencode
COPY --chown=root:root config/agents/codex /usr/local/sbin/codex
RUN chmod +x /usr/local/sbin/claude /usr/local/sbin/opencode /usr/local/sbin/codex

#========================================#
# Finalise build
#========================================#

# Switch to root user
USER root

# Create cvmfs keys and data directories
RUN mkdir -p /etc/cvmfs/keys/ardc.edu.au \
    && mkdir -p /data /neurodesktop-storage \
    && chown ${NB_UID}:${NB_GID} /neurodesktop-storage \
    # Mode 0770 (owner jovyan:users) denied write access to HPC-style
    # unprivileged users whose UID is NOT 1000 and GID is NOT 100 - and on
    # real HPC Apptainer the directory is usually bind-mounted from a
    # per-user scratch dir anyway, so world-write is the realistic default.
    # Was breaking test_crud's /neurodesktop-storage parametrisation in the
    # HPC simulation CI job (UID 5000 could neither read nor write).
    && chmod 0777 /neurodesktop-storage
COPY config/cvmfs/neurodesk.ardc.edu.au.pub /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
COPY config/cvmfs/neurodesk.ardc.edu.au.conf* /etc/cvmfs/config.d/
COPY config/cvmfs/default.local /etc/cvmfs/default.local

# Install neurocommand
ADD "https://api.github.com/repos/neurodesk/neurocommand/git/refs/heads/main" /tmp/skipcache
RUN rm /tmp/skipcache \
    && git clone https://github.com/neurodesk/neurocommand.git /neurocommand \
    && cd /neurocommand \
    && sed -i 's|CONTAINER_PATH=${PATH_PREFIX}/containers|CONTAINER_PATH=${NEURODESKTOP_LOCAL_CONTAINERS:-${PATH_PREFIX}/containers}|g' neurodesk/fetch_containers.sh \
    && sed -i 's|export CONTAINER_PATH="${_base}"/containers|export CONTAINER_PATH="${NEURODESKTOP_LOCAL_CONTAINERS:-${_base}/containers}"|g' neurodesk/fetch_and_run.sh \
    && sed -i 's|CONTAINER_PATH="${_base}"/containers|CONTAINER_PATH="${NEURODESKTOP_LOCAL_CONTAINERS:-${_base}/containers}"|g' neurodesk/fetch_and_run.sh \
    && bash build.sh --lxde --edit \
    && bash install.sh \
    && ln -s /neurodesktop-storage/containers /neurocommand/local/containers

# Start the container as root so docker-stacks runs before-notebook hooks with
# the privileges needed to bootstrap local Slurm/CVMFS, then drops to NB_USER.
USER root

# Bake the version into the image (CI passes the build date; local builds get "development").
# Placed at the end so earlier layers remain cacheable when only the version changes.
ARG NEURODESKTOP_VERSION="development"
ENV NEURODESKTOP_VERSION=${NEURODESKTOP_VERSION}

WORKDIR "/home/${NB_USER}"
