# Lmod's system profile can replace BASH_ENV before a login tool shell reads it.
# Only agent descendants have the saved initializer marker.
if [ -n "${NEURODESKTOP_PREVIOUS_BASH_ENV+x}" ]; then
    export BASH_ENV=/opt/neurodesktop/agent_bash_env.sh
fi
