FROM node:24.19.0-bookworm-slim AS node
RUN npm install --global @openai/codex@0.153.4 && npm cache clean --force

FROM python:3.12.12-slim-bookworm
ARG WORKER_UID=1000
ARG WORKER_GID=1000
RUN (getent group "$WORKER_GID" || groupadd --gid "$WORKER_GID" codex) \
    && (getent passwd "$WORKER_UID" || useradd --no-create-home --home-dir /tmp/home \
        --uid "$WORKER_UID" --gid "$WORKER_GID" codex)
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && apt-get update \
    && apt-get install -y --no-install-recommends bash git curl jq openssh-client acl ripgrep ca-certificates \
       gcc libc6-dev libx11-dev xvfb x11-xserver-utils \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.4.2 httpx==0.28.1 traitlets==5.14.3 \
       PyYAML==6.0.2 jupyter-server==2.17.0 jupyter-server-proxy==4.5.0 \
       astra-spec==0.0.14 astra-tools==0.2.18 anywidget==0.11.0 \
       jupyterhub==6.1.0 requests==2.34.2 ipykernel==7.4.0 websocket-client==1.9.2 \
       nibabel==5.4.2 numpy==2.5.4
RUN npm install --prefix /opt/launcher-dom --no-audit --no-fund \
       jsdom@26.1.0 typescript@5.2.2 esbuild@0.28.2 \
       @jupyterlab/docregistry@4.6.3 @jupyterlab/coreutils@6.6.3 \
       @jupyterlab/services@7.6.3 @lumino/widgets@2.9.0 \
    && npm cache clean --force
COPY javascript-lint/package.json javascript-lint/package-lock.json /opt/javascript-lint/
RUN npm ci --prefix /opt/javascript-lint --ignore-scripts --no-audit --no-fund \
    && npm cache clean --force
RUN ln -s /usr/local/bin/python3 /usr/bin/python3
ENV NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES=/opt/launcher-dom/node_modules
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
