#!/bin/bash
# set -e

SERVERADDRESS=$1

wget https://raw.githubusercontent.com/NeuroDesk/neurocommand/main/cvmfs/log.txt

mapfile -t arr < log.txt
for LINE in "${arr[@]}";
do
    echo "LINE: $LINE"
    IMAGENAME_BUILDDATE="$(cut -d' ' -f1 <<< "${LINE}")"
    echo "IMAGENAME_BUILDDATE: $IMAGENAME_BUILDDATE"

    
    if curl --output /dev/null --silent --head --fail "${SERVERADDRESS}${IMAGENAME_BUILDDATE}.simg"; then
            echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg exists in ${SERVERADDRESS}"
    else
            echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg does not exist yet in ${SERVERADDRESS}. Something is WRONG"
            exit 2
    fi
done

rm log.txt