# shellcheck shell=sh
# system-wide profile.modules                                          #
# Initialize modules for all sh-derivative shells                      #
#----------------------------------------------------------------------#
trap "" 1 2 3

case "$0" in
    -bash|bash|*/bash)
        # Lmod initialization is installed by the image package.
        # shellcheck source=/dev/null
        . /usr/share/lmod/lmod/init/bash
        ;;
       -ksh|ksh|*/ksh)
        # Lmod initialization is installed by the image package.
        # shellcheck source=/dev/null
        . /usr/share/lmod/lmod/init/ksh
        ;;
       -zsh|zsh|*/zsh)
        # Lmod initialization is installed by the image package.
        # shellcheck source=/dev/null
        . /usr/share/lmod/lmod/init/zsh
        ;;
          -sh|sh|*/sh)
        # Lmod initialization is installed by the image package.
        # shellcheck source=/dev/null
        . /usr/share/lmod/lmod/init/sh
        ;;
                    *)
        # Lmod initialization is installed by the image package.
        # shellcheck source=/dev/null
        . /usr/share/lmod/lmod/init/sh
        ;;  # default for scripts
esac

trap - 1 2 3
