# Sourced by agent launchers, including POSIX sh protocol wrappers.
# Defer initialization to tool shells so agent protocol streams stay quiet.
if [ "${BASH_ENV:-}" != /opt/neurodesktop/agent_bash_env.sh ]; then
    export NEURODESKTOP_PREVIOUS_BASH_ENV="${BASH_ENV:-}"
fi
export BASH_ENV=/opt/neurodesktop/agent_bash_env.sh
