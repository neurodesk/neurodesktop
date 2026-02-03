#things in .bashrc get executed for every subshell
if [ -f '/usr/share/module.sh' ]; then source /usr/share/module.sh; fi

if [ -f '/opt/neurodesktop/environment_variables.sh' ]; then source /opt/neurodesktop/environment_variables.sh; fi

command_not_found_handle() {
    echo "Use ml <tool>/<version> to load module into existing terminal. Or open a new terminal after loading module from sidebar"
    echo "bash: $1: command not found"
    return 127
}
