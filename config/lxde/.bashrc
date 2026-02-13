#things in .bashrc get executed for every subshell
if [ -f '/usr/share/module.sh' ]; then source /usr/share/module.sh; fi

if [ -f '/opt/neurodesktop/environment_variables.sh' ]; then source /opt/neurodesktop/environment_variables.sh; fi

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

command_not_found_handle() {
    echo "Use ml <tool>/<version> to load module into existing terminal. Or open a new terminal after loading module from sidebar"
    echo "bash: $1: command not found"
    return 127
}
