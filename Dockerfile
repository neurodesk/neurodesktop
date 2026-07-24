# syntax=docker/dockerfile:1.25

# Pin to a specific jupyter/base-notebook date for reproducibility.
# https://quay.io/repository/jupyter/base-notebook?tab=tags
ARG BASE_IMAGE_TAG=2026-06-29
ARG APPTAINER_VERSION=1.5.2
ARG APPTAINER_GO_VERSION=1.26.4
ARG APPTAINER_GRPC_VERSION=1.82.0

FROM golang:${APPTAINER_GO_VERSION}-bookworm AS apptainer

ARG APPTAINER_VERSION
ARG APPTAINER_GRPC_VERSION

COPY --chmod=0755 scripts/apt_install_retry.sh /usr/local/bin/apt-install-retry

RUN apt-install-retry \
    autoconf \
    automake \
    build-essential \
    ca-certificates \
    cryptsetup \
    curl \
    fakeroot \
    git \
    libattr1-dev \
    libfuse3-dev \
    liblzo2-dev \
    liblz4-dev \
    liblzma-dev \
    libprotobuf-c-dev \
    libseccomp-dev \
    libsubid-dev \
    libtalloc-dev \
    libtool \
    libzstd-dev \
    pkg-config \
    tzdata \
    uidmap \
    wget \
    zlib1g-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    git clone --depth 1 --branch "v${APPTAINER_VERSION}" https://github.com/apptainer/apptainer.git /tmp/apptainer \
    && cd /tmp/apptainer \
    && for i in 1 2 3; do \
        go get "google.golang.org/grpc@v${APPTAINER_GRPC_VERSION}" \
        && go mod tidy \
        && go mod download \
        && break \
        || { if [ "$i" -eq 3 ]; then exit 1; fi; sleep 5; }; \
    done \
    && for attempt in 1 2 3 4 5; do \
        if ./scripts/download-dependencies; then \
            break; \
        fi; \
        if [ "$attempt" -eq 5 ]; then \
            echo "download-dependencies failed after ${attempt} attempts." >&2; \
            exit 1; \
        fi; \
        echo "download-dependencies attempt ${attempt}/5 failed; retrying." >&2; \
        sleep "$((attempt * 10))"; \
    done \
    && ./scripts/compile-dependencies \
    && printf '%s\n' "${APPTAINER_VERSION}" > VERSION \
    && ./mconfig --prefix=/opt/apptainer --with-suid \
    && make -C builddir \
    && make -C builddir install \
    && ./scripts/install-dependencies \
    && /opt/apptainer/bin/apptainer --version \
    && rm -rf /tmp/apptainer

###############################################################################
# Stage 1: Builder — compile Guacamole server, patch code-server, install codex
###############################################################################
FROM quay.io/jupyter/base-notebook:${BASE_IMAGE_TAG} AS builder

USER root

ARG BUILD_ONLY_APT_PACKAGES="build-essential libcairo2-dev libjpeg-turbo8-dev libpng-dev libtool-bin freerdp2-dev libvncserver-dev libssl-dev libwebp-dev libssh2-1-dev libpango1.0-dev"
ARG GUACAMOLE_VERSION="1.6.0"
ARG CODE_SERVER_VERSION="4.126.0"

COPY --chmod=0755 scripts/apt_install_retry.sh /usr/local/bin/apt-install-retry

# Install build dependencies + nodejs for npm operations
RUN apt-install-retry \
    ${BUILD_ONLY_APT_PACKAGES} \
    wget \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-install-retry nodejs yarn \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Build Guacamole server
RUN curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 300 \
    "https://archive.apache.org/dist/guacamole/${GUACAMOLE_VERSION}/source/guacamole-server-${GUACAMOLE_VERSION}.tar.gz" \
    -o /tmp/guacamole-server-${GUACAMOLE_VERSION}.tar.gz \
    && tar xvf /tmp/guacamole-server-${GUACAMOLE_VERSION}.tar.gz -C /tmp \
    && rm /tmp/guacamole-server-${GUACAMOLE_VERSION}.tar.gz \
    && cd /tmp/guacamole-server-${GUACAMOLE_VERSION} \
    && ./configure --with-init-dir=/etc/init.d \
    && make \
    && make install \
    && ldconfig \
    && rm -r /tmp/guacamole-server-${GUACAMOLE_VERSION}

# Install code-server as a prebuilt binary and patch bundled npm dependencies.
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
    # Keep basic-ftp current in case code-server's shrinkwrap lags the patched package.
    cd /opt/code-server; \
    npm update --no-audit --no-fund basic-ftp; \
    # Ensure bundled tar is patched against CVE-2026-59873 until code-server ships 7.5.19+.
    npm update --no-audit --no-fund tar; \
    # Patch VS Code's nested shell-quote copy until code-server bundles 1.8.4+.
    shell_quote_tar="$(npm pack --silent shell-quote@1.8.4)"; \
    shell_quote_dir="/opt/code-server/lib/vscode/node_modules/shell-quote"; \
    rm -rf "${shell_quote_dir}"; \
    mkdir -p "${shell_quote_dir}"; \
    tar -xzf "${shell_quote_tar}" -C "${shell_quote_dir}" --strip-components=1; \
    rm -f "${shell_quote_tar}"; \
    test "$(node -p 'require("/opt/code-server/lib/vscode/node_modules/shell-quote/package.json").version')" = "1.8.4"; \
    rm -f "/tmp/${cs_tar}"; \
    npm cache clean --force



###############################################################################
# Stage 2: Final runtime image
###############################################################################
FROM quay.io/jupyter/base-notebook:${BASE_IMAGE_TAG}

LABEL maintainer="Neurodesk Project <www.neurodesk.org>"

USER root

ARG GUACAMOLE_RUNTIME_APT_PACKAGES="libfreerdp2-2t64 libfreerdp-client2-2t64 libwinpr2-2t64 libvncclient1"

COPY --chmod=0755 scripts/apt_install_retry.sh /usr/local/bin/apt-install-retry
# Generic retry wrapper for flaky non-apt external downloads (curl|bash
# installers, conda, git clone) that lack built-in retry — same spirit as the
# `harden download` curl --retry pattern used elsewhere in this file.
COPY --chmod=0755 scripts/retry.sh /usr/local/bin/retry

#========================================#
# Core services
#========================================#


# Install base image dependencies (runtime only — build deps are in the builder stage)
RUN apt-install-retry \
    software-properties-common \
    openjdk-21-jre-headless \
    ${GUACAMOLE_RUNTIME_APT_PACKAGES} \
    tigervnc-common \
    tigervnc-standalone-server \
    tigervnc-tools \
    xorgxrdp \
    xrdp \
    lxde \
    autocutsel \
    gvfs \
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
# Use tar round-trip to preserve symlinks (cp/COPY dereference them, tripling
# disk usage and producing ldconfig warnings).
RUN --mount=type=bind,from=builder,source=/,target=/tmp/builder-root,ro \
    cp /tmp/builder-root/usr/local/sbin/guacd /usr/local/sbin/guacd \
    && cp /tmp/builder-root/etc/init.d/guacd /etc/init.d/guacd \
    && cd /tmp/builder-root/usr/local/lib \
    && tar cf - libguac* | tar xf - -C /usr/local/lib/ \
    && ldconfig

# Copy code-server from builder (already patched)
RUN --mount=type=bind,from=builder,source=/opt/code-server,target=/tmp/code-server,ro \
    cp -a /tmp/code-server /opt/code-server \
    && ln -sf /opt/code-server/bin/code-server /usr/local/bin/code-server

# add a static strace executable to /opt which we can copy to containers for debugging:
RUN mkdir -p /opt/strace \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 300 \
    "https://github.com/JuliaBinaryWrappers/strace_jll.jl/releases/download/strace-v6.7.0%2B1/strace.v6.7.0.x86_64-linux-gnu.tar.gz" \
    -o /tmp/strace.tar.gz \
    && tar xzf /tmp/strace.tar.gz -C /opt/strace --strip-components=1 \
    && rm /tmp/strace.tar.gz \
    && chmod +x /opt/strace

ARG TOMCAT_REL="11"
ARG TOMCAT_VERSION="11.0.23"
ARG TOMCAT_MIGRATION_VERSION="1.0.12"
ARG GUACAMOLE_VERSION="1.6.0"
ENV LANG=""
ENV LANGUAGE=""
ENV LC_ALL=""

# Install the source-built Apptainer tree from the build stage. This avoids the
# Launchpad API/PPA path and lets us pin scanner-fixed Go module/toolchain levels
# before upstream publishes a matching multi-arch runtime image.
COPY --from=apptainer /opt/apptainer /opt/apptainer
# apptainer is built --with-suid but runs setuid-DISABLED (see the apptainer.conf edit below), so
# unprivileged image builds fall back to the bundled proot (libexec/apptainer/bin/proot). proot is
# dynamically linked and, because it's vendored (not apt-installed), nothing pulls in its runtime
# libs. Ship libtalloc2 + libprotobuf-c1 so the proot fallback works on hosts/sessions without user
# namespaces — otherwise `apptainer build` / `singularity` fails at the mksquashfs step with
# "proot: error while loading shared libraries: libtalloc.so.2 / libprotobuf-c.so.1".
RUN ln -sf /opt/apptainer/bin/apptainer /usr/local/bin/apptainer \
    && ln -sf /opt/apptainer/bin/singularity /usr/local/bin/singularity \
    && rm -rf /opt/apptainer/libexec/apptainer/cni \
    && sed -i 's/^allow setuid = yes/allow setuid = no/' /opt/apptainer/etc/apptainer/apptainer.conf \
    && apt-install-retry fuse-overlayfs squashfuse libtalloc2 libprotobuf-c1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && rm -rf /root/.cache && rm -rf /home/${NB_USER}/.cache

# Install Apache Tomcat
RUN retry wget -q https://archive.apache.org/dist/tomcat/tomcat-${TOMCAT_REL}/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz -P /tmp \
    && tar -xf /tmp/apache-tomcat-${TOMCAT_VERSION}.tar.gz -C /tmp \
    && rm -rf /tmp/apache-tomcat-${TOMCAT_VERSION}.tar.gz \
    && mv /tmp/apache-tomcat-${TOMCAT_VERSION} /usr/local/tomcat \
    && mv /usr/local/tomcat/webapps /usr/local/tomcat/webapps.dist \
    && mkdir /usr/local/tomcat/webapps \
    && sed -i -E '/<Connector port="8080" protocol="HTTP\/1\.1"/ {/maxHttpRequestHeaderSize=/! s|$| maxHttpRequestHeaderSize="65536"|;}' /usr/local/tomcat/conf/server.xml \
    && grep -q 'maxHttpRequestHeaderSize="65536"' /usr/local/tomcat/conf/server.xml \
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

# Install Apache Guacamole WAR and convert its Java EE servlet APIs for Tomcat 11.
RUN curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 300 \
    "https://archive.apache.org/dist/guacamole/${GUACAMOLE_VERSION}/binary/guacamole-${GUACAMOLE_VERSION}.war" \
    -o /tmp/guacamole-${GUACAMOLE_VERSION}.war \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 300 \
    "https://archive.apache.org/dist/tomcat/jakartaee-migration/v${TOMCAT_MIGRATION_VERSION}/binaries/jakartaee-migration-${TOMCAT_MIGRATION_VERSION}-shaded.jar" \
    -o /tmp/jakartaee-migration-${TOMCAT_MIGRATION_VERSION}-shaded.jar \
    && java -jar /tmp/jakartaee-migration-${TOMCAT_MIGRATION_VERSION}-shaded.jar \
    /tmp/guacamole-${GUACAMOLE_VERSION}.war \
    /usr/local/tomcat/webapps/ROOT.war \
    && rm -f /tmp/guacamole-${GUACAMOLE_VERSION}.war \
    /tmp/jakartaee-migration-${TOMCAT_MIGRATION_VERSION}-shaded.jar

# #========================================#
# # Software (as root user)
# #========================================#

# Workaround for CVMFS to break systemctl by replacing it with a dummy script
RUN mv /usr/bin/systemctl /usr/bin/systemctl.orig \
    && echo '#!/bin/bash' > /usr/bin/systemctl \
    && echo 'echo "systemctl is disabled in this container"' >> /usr/bin/systemctl \
    && chmod +x /usr/bin/systemctl

# Install CVMFS
RUN retry wget -q https://cvmrepo.s3.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb -P /tmp \
    && dpkg -i /tmp/cvmfs-release-latest_all.deb \
    && rm /tmp/cvmfs-release-latest_all.deb \
    && apt-install-retry \
    autofs \
    cvmfs \
    uuid-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Tools and Libs
RUN apt-install-retry \
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
    retry curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 300 \
    https://raw.githubusercontent.com/rstudio/tinytex/master/tools/install-bin-unix.sh \
    -o /tmp/install-bin-unix.sh && bash /tmp/install-bin-unix.sh; \
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
    && retry bash -o pipefail -c 'curl -fsSL https://get.nextflow.io | bash' \
    && mv /tmp/nextflow /usr/local/bin/nextflow \
    && chmod 755 /usr/local/bin/nextflow \
    && retry bash -o pipefail -c 'wget -qO- https://get.nf-test.com | bash -s 0.9.5' \
    && test -f "${HOME}/.nf-test/nf-test.jar" \
    && cp -a "${HOME}/.nf-test/." "${NF_TEST_HOME}/" \
    && printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'exec java -jar /opt/nf-test/nf-test.jar "$@"' > /usr/local/bin/nf-test \
    && chmod 755 /usr/local/bin/nf-test \
    && mkdir -p /opt/nf-neuro \
    && retry git clone --depth=1 https://github.com/nf-neuro/modules.git "${NF_NEURO_MODULES_DIR}" \
    && chown -R ${NB_UID}:${NB_GID} /opt/nf-neuro "${NF_TEST_HOME}" "${HOME}/.nextflow" \
    && rm -rf /root/.cache "${HOME}/.nf-test" /tmp/nf-test /tmp/nextflow

# Install build tools temporarily — nodejs is needed by codex CLI at runtime and
# by hatch-jupyter-builder to compile JupyterLab extensions from source (e.g.
# jupyterlab-slurm, neurodesk-launcher). build-essential provides gcc for pip
# packages with C extensions (e.g. psutil, traits). build-essential is removed
# after extensions are built (see purge step below); nodejs stays for codex.
RUN retry bash -o pipefail -c 'curl -fsSL https://deb.nodesource.com/setup_24.x | bash -' \
    && apt-install-retry nodejs build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install AI coding assistants
RUN npm_config_cache=/tmp/npm-root-cache npm install -g @openai/codex \
    && rm -rf /root/.npm /tmp/npm-root-cache /home/${NB_USER}/.npm \
    && su - "${NB_USER}" -c 'retry bash -o pipefail -c "curl -fsSL https://claude.ai/install.sh | bash -s -- stable"' \
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
RUN retry bash -o pipefail -c 'curl -fsSL https://opencode.ai/install | bash' \
    && mv /home/jovyan/.opencode/bin/opencode /usr/bin/opencode \
    && rm -rf /home/${NB_USER}/.cache /home/${NB_USER}/.local

# Install Firefox from Mozilla's official apt repository. This avoids both the
# Launchpad API and Ubuntu's snap-backed firefox package.
RUN --mount=type=bind,source=config/firefox,target=/tmp/firefox,ro \
    install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 20 https://packages.mozilla.org/apt/repo-signing-key.gpg \
    -o /etc/apt/keyrings/packages.mozilla.org.asc \
    && install -d -m 0700 /tmp/mozilla-gnupg \
    && GNUPGHOME=/tmp/mozilla-gnupg gpg -n -q --import --import-options import-show /etc/apt/keyrings/packages.mozilla.org.asc \
    | awk '/pub/{getline; gsub(/^ +| +$/, ""); if ($0 != "35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3") exit 1}' \
    && rm -rf /tmp/mozilla-gnupg \
    && printf '%s\n' 'deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main' \
    > /etc/apt/sources.list.d/mozilla.list \
    && install -m 0644 /tmp/firefox/mozilla /etc/apt/preferences.d/mozilla \
    && apt-install-retry firefox \
    && install -m 0755 /tmp/firefox/neurodesktop-firefox /usr/local/bin/neurodesktop-firefox \
    && ln -sf /usr/local/bin/neurodesktop-firefox /usr/local/bin/firefox \
    && sed -i -E \
    -e 's|^Exec=firefox$|Exec=/usr/local/bin/neurodesktop-firefox|' \
    -e 's|^Exec=firefox([[:space:]])|Exec=/usr/local/bin/neurodesktop-firefox\1|' \
    /usr/share/applications/firefox.desktop \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && rm -rf /home/${NB_USER}/.cache /home/${NB_USER}/.local
RUN --mount=type=bind,source=config/firefox/syspref.js,target=/tmp/syspref.js,ro \
    install -d -m 0755 /etc/firefox \
    && install -m 0644 /tmp/syspref.js /etc/firefox/syspref.js

#========================================#
# Software (as notebook user)
#========================================#

USER ${NB_USER}

# Install conda packages
RUN retry conda install -c conda-forge nb_conda_kernels \
    && conda clean --all -f -y \
    && conda config --system --prepend envs_dirs '~/conda-environments' \
    && rm -rf /home/${NB_USER}/.cache

# Install Python packages and JupyterLab extensions
ARG BUST_CACHE_PIP=3
RUN /opt/conda/bin/pip install \
    datalad \
    nipype \
    niwrap \
    nbdev \
    nf-core \
    snakemake \
    pydra==1.0a9 \
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
    # Pinned: patch_nbi.py rewrites this exact release's labextension bundle;
    # bump both together after re-verifying the patch.
    notebook_intelligence==5.2.1 \
    jupyterlab_rise \
    jupyterlab-niivue==0.2.7 \
    jupyterlab_myst \
    jupyter-sshd-proxy \
    papermill \
    ipycanvas \
    jupyter-resource-usage \
    jupyter_scheduler \
    jupyterlab-slurm@git+https://github.com/NERSC/jupyterlab-slurm.git@main \
    httpx \
    ipywidgets==8.1.8 \
    ipyvolume \
    jupyterlab_widgets \
    nbgitpuller \
    xnat \
    pytest \
    bash_kernel \
    "packaging>=26.0" \
    "requests>=2.34.2" \
    "chardet<8" \
    && /opt/conda/bin/pip install --upgrade "litellm>=1.85.0" \
    && /opt/conda/bin/python -m bash_kernel.install --sys-prefix \
    && /opt/conda/bin/jupyter labextension disable @jupyterlab/apputils-extension:announcements \
    && rm -rf /home/${NB_USER}/.cache

# Build and install neurodesk-launcher JupyterLab extension
RUN --mount=type=bind,source=extensions/neurodesk-launcher,target=/tmp/neurodesk-launcher-src,ro \
    rm -rf /tmp/neurodesk-launcher \
    && mkdir -p /tmp/neurodesk-launcher \
    && cp -R /tmp/neurodesk-launcher-src/. /tmp/neurodesk-launcher/ \
    && cd /tmp/neurodesk-launcher \
    && npm_config_cache=/tmp/neurodesk-launcher-npm-cache /opt/conda/bin/pip install . \
    && /opt/conda/bin/jupyter labextension disable @jupyterhub/jupyter-server-proxy \
    && rm -rf /tmp/neurodesk-launcher /tmp/neurodesk-launcher-npm-cache /home/${NB_USER}/.cache

#========================================#
# Configuration (as root user)
#========================================#

USER root

# Remove build-time packages: -dev headers for pip native extensions and
# build-essential for C extensions. nodejs stays — codex CLI needs it at
# runtime. Keep the libc dev chain because cvmfs and uuid-dev depend on it.
# (Guacamole build deps are already excluded via multi-stage build.)
RUN apt-mark manual autofs cvmfs libc6-dev linux-libc-dev uuid-dev \
    && DEBIAN_FRONTEND=noninteractive apt-get purge --yes --auto-remove \
    libgpgme-dev libossp-uuid-dev build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# The kernel-spec rewrite below embeds this path in every kernelspec. Keep this
# cache boundary narrow; bulky local runtime config is installed after
# neurocommand so webapp/link edits do not force the rest of the image to rebuild.
RUN --mount=type=bind,source=config/jupyter/kernel_wrapper.sh,target=/tmp/kernel_wrapper.sh,ro \
    install -D -m 0755 /tmp/kernel_wrapper.sh /opt/neurodesktop/kernel_wrapper.sh

# Create Guacamole configurations (user-mapping.xml gets filled in the startup.sh script)
RUN --mount=type=bind,source=config/guacamole,target=/tmp/guacamole,ro \
    mkdir -p /etc/guacamole \
    && echo -e "user-mapping: /etc/guacamole/user-mapping.xml\nguacd-hostname: 127.0.0.1" > /etc/guacamole/guacamole.properties \
    && echo -e "[server]\nbind_host = 127.0.0.1\nbind_port = 4822" > /etc/guacamole/guacd.conf \
    && install -m 0644 -o ${NB_UID} -g ${NB_GID} /tmp/guacamole/user-mapping-vnc.xml /etc/guacamole/user-mapping-vnc.xml \
    && install -m 0644 -o ${NB_UID} -g ${NB_GID} /tmp/guacamole/user-mapping-vnc-rdp.xml /etc/guacamole/user-mapping-vnc-rdp.xml \
    && ln -sf /etc/guacamole/user-mapping-vnc.xml /etc/guacamole/user-mapping.xml \
    # Safari has no persistable clipboard-read permission, so Guacamole's
    # focus-driven clipboard sync fails there. Inject a shim that reads the
    # clipboard inside the Cmd+V paste gesture instead (see the shim header).
    && install -m 0644 /tmp/guacamole/mac-clipboard-shim.js /usr/local/tomcat/webapps/ROOT/mac-clipboard-shim.js \
    # Content-hash cache buster: browsers cache the shim URL, so a changed
    # shim must get a changed URL or upgraded images serve stale scripts.
    && SHIM_V="$(sha256sum /usr/local/tomcat/webapps/ROOT/mac-clipboard-shim.js | cut -c1-10)" \
    && sed -i "s|</body>|<script src=\"mac-clipboard-shim.js?v=${SHIM_V}\"></script></body>|" /usr/local/tomcat/webapps/ROOT/index.html \
    && grep -q "mac-clipboard-shim.js?v=${SHIM_V}" /usr/local/tomcat/webapps/ROOT/index.html \
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

# Create defaults directory structure and copy default home files.
RUN --mount=type=bind,source=config/itksnap,target=/tmp/itksnap,ro \
    --mount=type=bind,source=config/lxde,target=/tmp/lxde,ro \
    --mount=type=bind,source=config/vscode,target=/tmp/vscode,ro \
    --mount=type=bind,source=config/conda,target=/tmp/conda,ro \
    --mount=type=bind,source=config/agents,target=/tmp/agents,ro \
    --mount=type=bind,source=config/ssh/sshd_config,target=/tmp/sshd_config,ro \
    --mount=type=bind,source=config/jupyter/page_config.json,target=/tmp/page_config.json,ro \
    mkdir -p /opt/jovyan_defaults/.itksnap.org/ITK-SNAP \
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
    && mkdir -p /opt/jovyan_defaults/.jupyter/nbi/rules \
    && install -m 0644 /tmp/itksnap/UserPreferences.xml /opt/jovyan_defaults/.itksnap.org/ITK-SNAP/UserPreferences.xml \
    && install -m 0644 /tmp/lxde/mimeapps.list /opt/jovyan_defaults/.config/mimeapps.list \
    && install -m 0644 /tmp/lxde/panel /opt/jovyan_defaults/.config/lxpanel/LXDE/panels/panel \
    && install -m 0644 /tmp/vscode/settings.json /opt/jovyan_defaults/.local/share/code-server/User/settings.json \
    && install -m 0644 /tmp/lxde/libfm.conf /opt/jovyan_defaults/.config/libfm/libfm.conf \
    && install -m 0755 /tmp/lxde/xstartup /opt/jovyan_defaults/.vnc/xstartup \
    && install -m 0644 /tmp/lxde/75neurodesk-clipboard-sync /etc/X11/Xsession.d/75neurodesk-clipboard-sync \
    && install -m 0644 /tmp/conda/conda-readme.md /opt/jovyan_defaults/conda-readme.md \
    && install -m 0644 /tmp/agents/claude_settings.local.json /opt/jovyan_defaults/.claude/settings.local.json \
    && install -m 0644 /tmp/agents/claude_mcp_config.json /opt/jovyan_defaults/.claude/mcp_config.json \
    && install -m 0644 /tmp/agents/opencode_config.json /opt/jovyan_defaults/.config/opencode/opencode.json \
    && install -m 0644 /tmp/agents/codex_config.toml /opt/jovyan_defaults/.codex/config.toml \
    && install -m 0644 /tmp/sshd_config /opt/jovyan_defaults/.ssh/sshd_config \
    && install -m 0644 /tmp/page_config.json /opt/jovyan_defaults/.jupyter/labconfig/page_config.json \
    && install -m 0644 /tmp/agents/AGENTS_nbi.md /opt/jovyan_defaults/.jupyter/nbi/rules/neurodesk.md \
    && install -m 0644 /tmp/agents/nbi_config.json /opt/jovyan_defaults/.jupyter/nbi/config.json \
    && install -m 0644 /tmp/agents/nbi_mcp.json /opt/jovyan_defaults/.jupyter/nbi/mcp.json \
    && install -m 0644 /tmp/agents/nbi_tour_config.json /opt/jovyan_defaults/.jupyter/nbi/tour_config.json \
    && install -m 0755 /tmp/agents/nbi_setup.sh /opt/neurodesktop/nbi_setup.sh \
    && install -m 0644 /tmp/lxde/.bashrc /opt/jovyan_defaults/.bashrc_append \
    && /usr/bin/printf '%s\n%s\n%s\n' 'password' 'password' 'n' | vncpasswd /opt/jovyan_defaults/.vnc/passwd \
    && chown root:users /opt/jovyan_defaults/.vnc/passwd \
    && chmod 640 /opt/jovyan_defaults/.vnc/passwd

# Copy restore scripts, agent metadata, and wrapper scripts.
RUN --mount=type=bind,source=config/jupyter/restore_home_defaults.sh,target=/tmp/restore_home_defaults.sh,ro \
    --mount=type=bind,source=config/jupyter/update_page_config.py,target=/tmp/update_page_config.py,ro \
    --mount=type=bind,source=config/agents,target=/tmp/agents,ro \
    install -m 0755 -o root -g users /tmp/restore_home_defaults.sh /opt/neurodesktop/restore_home_defaults.sh \
    && install -m 0755 -o root -g users /tmp/update_page_config.py /opt/neurodesktop/update_page_config.py \
    && install -D -m 0644 /tmp/agents/AGENTS.md /opt/AGENTS.md \
    && install -m 0755 -o root -g root /tmp/agents/claude /usr/local/sbin/claude \
    && install -m 0755 -o root -g root /tmp/agents/opencode /usr/local/sbin/opencode \
    && install -m 0755 -o root -g root /tmp/agents/codex /usr/local/sbin/codex \
    # Anchored Notebook Intelligence patch (see patch_nbi.py): make the
    # settings panel fetch fresh capabilities on open instead of auto-saving
    # its stale client-side cache over the OpenCode model sync. The script
    # fails the build when the anchor no longer matches.
    && install -m 0755 -o root -g users /tmp/agents/patch_nbi.py /opt/neurodesktop/patch_nbi.py \
    && /opt/conda/bin/python3 /opt/neurodesktop/patch_nbi.py

#========================================#
# Finalise build
#========================================#

# Switch to root user
USER root

# Create cvmfs keys and data directories
RUN --mount=type=bind,source=config/cvmfs,target=/tmp/cvmfs,ro \
    mkdir -p /etc/cvmfs/keys/ardc.edu.au /etc/cvmfs/config.d \
    && mkdir -p /data /neurodesktop-storage \
    && chown ${NB_UID}:${NB_GID} /neurodesktop-storage \
    # Mode 0770 (owner jovyan:users) denied write access to HPC-style
    # unprivileged users whose UID is NOT 1000 and GID is NOT 100 - and on
    # real HPC Apptainer the directory is usually bind-mounted from a
    # per-user scratch dir anyway, so world-write is the realistic default.
    # Was breaking test_crud's /neurodesktop-storage parametrisation in the
    # HPC simulation CI job (UID 5000 could neither read nor write).
    && chmod 0777 /neurodesktop-storage \
    && install -m 0644 /tmp/cvmfs/neurodesk.ardc.edu.au.pub /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub \
    && cp /tmp/cvmfs/neurodesk.ardc.edu.au.conf* /etc/cvmfs/config.d/ \
    && chmod 0644 /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf* \
    && install -m 0644 /tmp/cvmfs/default.local /etc/cvmfs/default.local

# Install neurocommand
ARG NEUROCOMMAND_REF=main
RUN echo "Installing neurocommand ref ${NEUROCOMMAND_REF}" \
    && retry git clone https://github.com/neurodesk/neurocommand.git /neurocommand \
    && cd /neurocommand \
    && git checkout -B main "$NEUROCOMMAND_REF" \
    && git branch --set-upstream-to=origin/main main \
    && bash build.sh --lxde --edit \
    && bash install.sh \
    && ln -s /neurodesktop-storage/containers /neurocommand/local/containers

# Install local runtime configuration late. This layer intentionally sits after
# neurocommand so launcher/webapp config edits do not invalidate Guacamole,
# defaults, CVMFS, or the neurocommand clone/install layer.
RUN --mount=type=bind,source=config/jupyter,target=/tmp/jupyter,ro \
    --mount=type=bind,source=config/guacamole,target=/tmp/guacamole,ro \
    --mount=type=bind,source=config/ssh,target=/tmp/ssh,ro \
    --mount=type=bind,source=config/slurm,target=/tmp/slurm,ro \
    --mount=type=bind,source=config/lxde,target=/tmp/lxde,ro \
    --mount=type=bind,source=config/lmod,target=/tmp/lmod,ro \
    --mount=type=bind,source=scripts/generate_jupyter_config.py,target=/tmp/generate_jupyter_config.py,ro \
    --mount=type=bind,source=tests,target=/tmp/tests,ro \
    --mount=type=bind,source=Dockerfile,target=/tmp/Dockerfile,ro \
    install -D -m 0644 /tmp/jupyter/neurodesk_brain_logo.svg /opt/neurodesk_brain_logo.svg \
    && install -D -m 0644 /tmp/jupyter/neurodesk_brain_icon.svg /opt/neurodesk_brain_icon.svg \
    && install -D -m 0644 /tmp/jupyter/vscode_logo.svg /opt/vscode_logo.svg \
    && install -d -m 0755 /opt/neurodesk/icons \
    && cp -a /tmp/jupyter/webapp_icons/. /opt/neurodesk/icons/ \
    && install -D -m 0644 /tmp/jupyter/webapp_links.json /opt/config/jupyter/webapp_links.json \
    && install -d -m 0755 /opt/config/jupyter/webapp_icons \
    && cp -a /tmp/jupyter/webapp_icons/. /opt/config/jupyter/webapp_icons/ \
    && install -D -m 0644 /tmp/lxde/background.png /usr/share/lxde/wallpapers/desktop_wallpaper.png \
    && install -D -m 0644 /tmp/lxde/pcmanfm.conf /etc/xdg/pcmanfm/LXDE/pcmanfm.conf \
    && install -D -m 0644 /tmp/lxde/lxterminal.conf /usr/share/lxterminal/lxterminal.conf \
    && install -D -m 0644 /tmp/lmod/module.sh /usr/share/module.sh \
    && install -D -m 0644 /tmp/lxde/rc.xml /etc/xdg/openbox/rc.xml \
    && sed -i 's/#user_allow_other/user_allow_other/g' /etc/fuse.conf \
    && rm -f /usr/bin/lxpolkit \
    && chmod +x /usr/bin/fusermount \
    && mkdir -p /usr/local/bin/start-notebook.d /usr/local/bin/before-notebook.d /opt/neurodesktop/scripts /opt/neurodesktop/webapp_wrapper \
    && install -m 0755 /tmp/jupyter/start_notebook.sh /usr/local/bin/start-notebook.d/start_notebook.sh \
    && install -m 0755 /tmp/jupyter/before_notebook.sh /usr/local/bin/before-notebook.d/before_notebook.sh \
    && install -m 0755 /tmp/jupyter/jupyterlab_startup.sh /opt/neurodesktop/jupyterlab_startup.sh \
    && install -m 0755 /tmp/jupyter/deferred_startup.sh /opt/neurodesktop/deferred_startup.sh \
    && install -m 0755 /tmp/jupyter/cvmfs_server_select.sh /opt/neurodesktop/cvmfs_server_select.sh \
    && install -m 0755 /tmp/guacamole/guacamole.sh /opt/neurodesktop/guacamole.sh \
    && install -m 0755 /tmp/guacamole/init_secrets.sh /opt/neurodesktop/init_secrets.sh \
    && install -m 0755 /tmp/guacamole/ensure_rdp_backend.sh /opt/neurodesktop/ensure_rdp_backend.sh \
    && install -m 0755 /tmp/jupyter/environment_variables.sh /opt/neurodesktop/environment_variables.sh \
    && install -m 0755 /tmp/jupyter/kernel_wrapper.sh /opt/neurodesktop/kernel_wrapper.sh \
    && install -m 0755 /tmp/jupyter/jupyterlmod_modulepath.py /opt/neurodesktop/jupyterlmod_modulepath.py \
    && install -m 0755 /tmp/jupyter/external_webapp_redirect.py /opt/neurodesktop/external_webapp_redirect.py \
    && install -m 0755 /tmp/ssh/ensure_sftp_sshd.sh /opt/neurodesktop/ensure_sftp_sshd.sh \
    && install -m 0755 /tmp/ssh/ensure_ssh_keys.sh /opt/neurodesktop/ensure_ssh_keys.sh \
    && install -m 0755 /tmp/slurm/setup_and_start_slurm.sh /opt/neurodesktop/setup_and_start_slurm.sh \
    && cp -a /tmp/tests /opt/tests \
    && install -m 0644 /tmp/Dockerfile /opt/tests/Dockerfile \
    && install -m 0755 /tmp/generate_jupyter_config.py /opt/neurodesktop/scripts/generate_jupyter_config.py \
    && cp -a /tmp/jupyter/webapp_wrapper/. /opt/neurodesktop/webapp_wrapper/ \
    && install -m 0755 /tmp/jupyter/webapp_launcher.sh /opt/neurodesktop/webapp_launcher.sh \
    # Associate office documents with the Neurodesk LibreOffice menu entries
    # and drop xarchiver's claim on them; must run after neurocommand has
    # generated its .desktop files.
    && python3 /tmp/lxde/update_office_mimeapps.py \
    /opt/jovyan_defaults/.config/mimeapps.list \
    /usr/share/applications/neurodesk \
    && update-desktop-database /usr/share/applications \
    && install -m 0644 /tmp/jupyter/jupyter_notebook_config.py.template /opt/neurodesktop/jupyter_notebook_config.py.template \
    && install -m 0644 /neurocommand/neurodesk/webapps.json /opt/neurodesktop/webapps.json \
    && python3 /opt/neurodesktop/scripts/generate_jupyter_config.py \
    /opt/neurodesktop/webapps.json \
    /opt/neurodesktop/jupyter_notebook_config.py.template \
    /etc/jupyter/jupyter_notebook_config.py \
    --merged-webapps-output /opt/neurodesktop/webapps.json \
    /tmp/jupyter/webapp_links.json \
    && chmod +rx /etc/jupyter/jupyter_notebook_config.py \
    /opt/neurodesktop/webapp_wrapper/webapp_wrapper.py \
    && chmod +r /opt/neurodesktop/webapp_wrapper/splash_template.html \
    /opt/neurodesktop/webapps.json \
    && chown -R root:users /opt/config /opt/neurodesktop /opt/tests


# Workaround for jupyterlab-rise + jupyterlab-myst incompatibility:
# jupyterlab-myst's federated bundle declares @jupyterlab/markdownviewer as a
# shared module it consumes from the host. RISE's bundle does not include that
# package in its shared scope, so MyST's plugins fail to instantiate and MyST
# directives (admonitions, dropdowns, etc.) render as raw markdown in slides.
# Rebuilding MyST with --core-path pointed at RISE's app directory embeds
# @jupyterlab/markdownviewer into MyST's own bundle, so it no longer asks the
# host for it. See https://github.com/jupyterlab-contrib/rise/issues/46
# Workaround for Node 24 file-open strictness: ensure safe-regex-test can resolve
# its @ljharb/tsconfig sibling during the webpack build so the ts-loader does not
# fail with ENOENT.
RUN MYST_VERSION="$(/opt/conda/bin/pip show jupyterlab_myst | awk '/^Version:/ {print $2}')" \
    && RISE_VERSION="$(/opt/conda/bin/pip show jupyterlab_rise | awk '/^Version:/ {print $2}')" \
    && MYST_PACKAGE_DIR="$(/opt/conda/bin/python -c 'import jupyterlab_myst, os; print(os.path.dirname(jupyterlab_myst.__file__))')" \
    && retry git clone --depth 1 --branch "v${MYST_VERSION}" https://github.com/jupyter-book/jupyterlab-myst.git /tmp/myst \
    && retry git clone --depth 1 --branch "v${RISE_VERSION}" https://github.com/jupyterlab-contrib/rise.git /tmp/rise \
    && cd /tmp/myst \
    && npm_config_cache=/tmp/myst-npm-cache npm install \
    && npm run build:css \
    && npm run build:lib \
    && mkdir -p /tmp/myst/node_modules/safe-regex-test/node_modules/@ljharb/tsconfig \
    && cp /tmp/myst/node_modules/@ljharb/tsconfig/tsconfig.json /tmp/myst/node_modules/safe-regex-test/node_modules/@ljharb/tsconfig/tsconfig.json 2>/dev/null || true \
    && /opt/conda/bin/jupyter labextension build --core-path=/tmp/rise/app . \
    && MYST_LABEXT_DIR="${MYST_PACKAGE_DIR}/labextension" \
    && APP_MYST_DIR=/opt/conda/share/jupyter/labextensions/jupyterlab-myst \
    && rm -rf "${MYST_LABEXT_DIR}" \
    && cp -a /tmp/myst/jupyterlab_myst/labextension "${MYST_LABEXT_DIR}" \
    && rm -rf "${APP_MYST_DIR}" \
    && cp -a "${MYST_LABEXT_DIR}" "${APP_MYST_DIR}" \
    && rm -rf /tmp/myst /tmp/rise /tmp/myst-npm-cache /home/${NB_USER}/.cache /home/${NB_USER}/.yarn

# Patch both nested tar copies after all npm-based build steps. Updating
# code-server's top-level dependency graph does not reach either scanner path.
ARG NODE_TAR_VERSION="7.5.19"
RUN set -eux; \
    node_tar_package="$(npm pack --silent "tar@${NODE_TAR_VERSION}")"; \
    for node_tar_dir in \
        /opt/code-server/lib/vscode/node_modules/tar \
        "$(npm root -g)/npm/node_modules/tar"; do \
        rm -rf "${node_tar_dir}"; \
        mkdir -p "${node_tar_dir}"; \
        tar -xzf "${node_tar_package}" -C "${node_tar_dir}" --strip-components=1; \
    done; \
    rm -f "${node_tar_package}"; \
    test "$(node -p 'require("/opt/code-server/lib/vscode/node_modules/tar/package.json").version')" = "${NODE_TAR_VERSION}"; \
    test "$(node -p 'require("/usr/lib/node_modules/npm/node_modules/tar/package.json").version')" = "${NODE_TAR_VERSION}"; \
    npm cache clean --force


# Start the container as root so docker-stacks runs before-notebook hooks with
# the privileges needed to bootstrap local Slurm/CVMFS, then drops to NB_USER.
USER root

WORKDIR "/home/${NB_USER}"

HEALTHCHECK --interval=1m --timeout=1s --start-period=3s --retries=3 CMD /etc/jupyter/docker_healthcheck.py || exit 1

# Image metadata
ARG BASE_IMAGE_TAG
ARG NEURODESKTOP_VERSION="development"
ARG BUILD_DATE="unknown"
ARG VCS_REF="unknown"
ENV NEURODESKTOP_VERSION=${NEURODESKTOP_VERSION}
LABEL org.opencontainers.image.title="neurodesktop" \
      org.opencontainers.image.description="Complete virtual desktop environment with GUI applications, ready to use in your browser or locally." \
      org.opencontainers.image.vendor="Neurodesk" \
      org.opencontainers.image.url="https://www.neurodesk.org" \
      org.opencontainers.image.documentation="https://www.neurodesk.org" \
      org.opencontainers.image.source="https://github.com/neurodesk/neurodesktop" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${NEURODESKTOP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.base.name="quay.io/jupyter/base-notebook:${BASE_IMAGE_TAG}"
