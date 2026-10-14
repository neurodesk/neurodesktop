#!/bin/bash
# Sourced by before_notebook.sh so the guarded endpoint reaches Jupyter.

# Guard against a black-holed OLLAMA_HOST. notebook_intelligence probes the
# Ollama API synchronously while Jupyter loads its server extensions, and a
# host that silently drops packets (e.g. host.docker.internal without a route
# on Linux) blocks server startup for 60s+. When the configured endpoint is
# not reachable within 1s, repoint this server process at 127.0.0.1, which
# refuses instantly (or is correct anyway when a local Ollama is running).
if [ -n "${OLLAMA_HOST:-}" ]; then
	if ! curl -so /dev/null --connect-timeout 1 --max-time 2 "${OLLAMA_HOST%/}/api/version" 2>/dev/null; then
		echo "[INFO] OLLAMA_HOST=${OLLAMA_HOST} is unreachable; using http://127.0.0.1:11434 for the Jupyter server so extension startup cannot block on it."
		export OLLAMA_HOST="http://127.0.0.1:11434"
	fi
fi
