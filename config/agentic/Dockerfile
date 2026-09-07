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
    && apt-get install -y --no-install-recommends bash git curl openssh-client ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.4.2 httpx==0.28.1 traitlets==5.14.3 \
       PyYAML==6.0.2 jupyter-server==2.17.0 \
       astra-spec==0.0.12 astra-tools==0.2.11 anywidget==0.11.0
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
