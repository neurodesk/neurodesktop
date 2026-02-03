#things in .bashrc get executed for every subshell
if [ -f '/usr/share/module.sh' ]; then source /usr/share/module.sh; fi

# Note: environment_variables.sh is sourced via /etc/bash.bashrc (set in Dockerfile)

command_not_found_handle() {
    echo "Use ml <tool>/<version> to load module into existing terminal. Or open a new terminal after loading module from sidebar"
    echo "bash: $1: command not found"
    return 127
}
