#!/bin/bash

# This file is sourced once in jupyterlab_startup.sh and once in ~/.bashrc so we get the same environment variables in the jupyter and in the desktop environment
if [[ -z "${NB_USER}" ]]; then
    export NB_USER=${USER}
fi

if [[ -z "${USER}" ]]; then
    export USER=${NB_USER}
fi

export MODULEPATH=/neurodesktop-storage/containers/modules/:/cvmfs/neurodesk.ardc.edu.au/containers/modules/

# Only setup MODULEPATH if a module system is installed
if [ -f '/usr/share/module.sh' ]; then
        export OFFLINE_MODULES=/neurodesktop-storage/containers/modules/
        export CVMFS_MODULES=/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/

        if [ ! -d $CVMFS_MODULES ]; then
                MODULEPATH=${OFFLINE_MODULES}
                export CVMFS_DISABLE=true
        else
                MODULEPATH=${CVMFS_MODULES}*
                export MODULEPATH=`echo $MODULEPATH | sed 's/ /:/g'`

                # if the offline modules directory exists, we can use it and will prefer it over cvmfs
                if [ -d ${OFFLINE_MODULES} ]; then
                        echo "Found local container installations in $OFFLINE_MODULES. Using installed containers with a higher priority over CVMFS."
                        export MODULEPATH=${OFFLINE_MODULES}:$MODULEPATH
                fi
        fi

        echo 'Neuroimaging tools are accessible via the Neurodesktop Applications menu and running them through the menu will provide help and setup instructions. If you are familiar with the tools and you want to combine multiple tools in one script, you can run "ml av" to see which tools are available and then use "ml <tool>/<version>" to load them. '
        
        # check if $CVMFS_DISABLE is set to true
        if [[ "$CVMFS_DISABLE" == "true" ]]; then
                echo "CVMFS is disabled. Using local containers stored in $MODULEPATH"
                if [ ! -d $MODULEPATH ]; then
                        echo 'Neurodesk tools not yet downloaded. Choose tools to install from the Neurodesktop Application menu.'
                fi
        fi
fi

# This also needs to be set in the Dockerfile, so it is available in a jupyter notebook
export APPTAINER_BINDPATH=/data,/mnt,/neurodesktop-storage,/tmp,/cvmfs
# This also needs to be set in the Dockerfile, so it is available in a jupyter notebook

export APPTAINERENV_SUBJECTS_DIR=${HOME}/freesurfer-subjects-dir
export MPLCONFIGDIR=${HOME}/.config/matplotlib-mpldir

export PATH=$PATH:${HOME}/.local/bin:/opt/conda/bin:/opt/conda/condabin

# This is needed to make containers writable as a workaround for macos with Apple Silicon. We need to do it here for the desktop
# and in the dockerfile for the jupyter notebook
export neurodesk_singularity_opts=" --overlay /tmp/apptainer_overlay "
# export neurodesk_singularity_opts=" -w " THIS DOES NOT WORK FOR SIMG FILES IN OFFLINE MODE
# There is a small delay in using --overlay in comparison to -w - maybe it would be faster to use a fixed size overlay file instead?

# !echo $neurodesk_singularity_opts
# test if the workaround is still needed: ml fsl; fslmaths or 
# import lmod
# await lmod.load('fsl/6.0.4')
# await lmod.list()
# !fslmaths

# # this adds --nv to the singularity calls -> but only if a GPU is present
# if [ "$(lspci | grep -i nvidia)" ]
# then
#         export neurodesk_singularity_opts="${neurodesk_singularity_opts} --nv "
# fi
# THIS IS CURRENTLY DISABLED BECAUSE IT CAUSES PROBLEMS ON UBUNTU 24.04 HOSTS WHERE THIS LEADS TO A GLIBC VERSION ERROR

export PS1='\u@neurodesktop-$NEURODESKTOP_VERSION:\w$ '

alias ll='ls -la'