# syntax=docker/dockerfile:1
ARG NEURODESKTOP_IMAGE=ghcr.io/neurodesk/neurodesktop:amd64
ARG KASM_IMAGE=kasmweb/core-ubuntu-noble:1.19.0@sha256:d1d9eccccbbd1995b4eda7ec891e14112860098d85d8c47d78b359db798fcd2c
FROM ${KASM_IMAGE} AS kasm
USER root
RUN /usr/bin/apt-get update && /usr/bin/apt-get install -y --no-install-recommends dpkg-repack \
    && mkdir /kasm-packages && cd /kasm-packages && dpkg-repack kasmvncserver \
    && cp -L /usr/bin/kasm-profile-sync /kasm-packages/kasm-profile-sync \
    && cp -L /usr/bin/kasm-profile-sync-2 /kasm-packages/kasm-profile-sync-2

FROM ${NEURODESKTOP_IMAGE}
ARG NEURODESKTOP_IMAGE
USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN --mount=from=kasm,source=/kasm-packages,target=/tmp/kasm-packages,ro \
    . /etc/os-release && test "$VERSION_CODENAME" = noble \
    && test "$(id -u jovyan)" = 1000 \
    && /usr/bin/apt-get update \
    && /usr/bin/apt-get install -y --no-install-recommends /tmp/kasm-packages/*.deb \
       ffmpeg pulseaudio pulseaudio-utils libarchive13t64 libdatetime-perl \
       libhash-merge-simple-perl liblist-moreutils-perl libtry-tiny-perl \
       libyaml-tiny-perl gettext ssl-cert x11-utils \
    && install -m 0755 /tmp/kasm-packages/kasm-profile-sync* /usr/bin/ \
    && rm -f /usr/local/bin/vncserver \
    && update-alternatives --set vncserver /usr/bin/kasmvncserver \
    && rm -rf /var/lib/apt/lists/*

COPY --from=kasm /dockerstartup/ /dockerstartup/
COPY --from=kasm /etc/kasmvnc/ /etc/kasmvnc/
RUN usermod --home /home/kasm-user jovyan \
    && mv /home/jovyan /home/kasm-default-profile \
    && ln -s /home/kasm-user /home/jovyan \
    && install -d -o 1000 -g 100 /home/kasm-user /var/run/pulse \
    && install -d -o 1000 -g 100 /home/kasm-default-profile/Desktop \
    && install -d -o 1000 -g 100 /usr/share/kasmvnc/www/Downloads \
    && ln -s /home/kasm-user/Downloads /usr/share/kasmvnc/www/Downloads/Downloads \
    && printf 'jovyan ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/notebook \
    && chmod 0440 /etc/sudoers.d/notebook \
    && printf '\nfor name in ("neurodesktop-vnc", "neurodesktop-rdp"):\n    c.ServerProxy.servers.pop(name, None)\n' >> /etc/jupyter/jupyter_notebook_config.py

COPY --chmod=0755 config/kasm/entrypoint.sh /opt/neurodesktop/kasm-entrypoint.sh
COPY --chmod=0755 config/kasm/custom_startup.sh /dockerstartup/custom_startup.sh
COPY --chmod=0755 config/kasm/open_jupyter.py /opt/neurodesktop/kasm-open-jupyter
COPY --chmod=0755 config/kasm/healthcheck.sh /opt/neurodesktop/kasm-healthcheck.sh
COPY --chmod=0644 config/kasm/jupyterlab.desktop /usr/share/applications/neurodesktop-jupyterlab.desktop
COPY --chmod=0644 tests/container/test_kasm_*.py /opt/tests/
RUN install -m 0755 -o 1000 -g 100 /usr/share/applications/neurodesktop-jupyterlab.desktop \
      /home/kasm-default-profile/Desktop/JupyterLab.desktop

ENV HOME=/home/kasm-user USER=jovyan \
    DISPLAY=:1 STARTUPDIR=/dockerstartup KASM_VNC_PATH=/usr/share/kasmvnc \
    NO_VNC_PORT=6901 VNC_PORT=5901 VNC_COL_DEPTH=24 VNC_RESOLUTION=1280x800 \
    MAX_FRAME_RATE=24 VNCOPTIONS="-PreferBandwidth -DynamicQualityMin=4 -DynamicQualityMax=7" \
    VNC_VIEW_ONLY_PW="" START_XFCE4=0 START_PULSEAUDIO=1 \
    PULSE_RUNTIME_PATH=/var/run/pulse AUDIO_PORT=4901 \
    KASM_SVC_PRINTER=0 KASM_SVC_SMARTCARD=0 KASM_SVC_GAMEPAD=0 \
    KASM_SVC_WEBCAM=0 KASM_SVC_RECORDER=0 \
    LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8
LABEL com.kasmweb.image="true" \
      org.opencontainers.image.title="Neurodesktop for Kasm Workspaces" \
      org.opencontainers.image.base.name="${NEURODESKTOP_IMAGE}"
EXPOSE 6901 4901 4902
WORKDIR /home/kasm-user
USER 1000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD ["/opt/neurodesktop/kasm-healthcheck.sh"]
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/neurodesktop/kasm-entrypoint.sh"]
CMD ["--wait"]
