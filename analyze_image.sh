#!/bin/bash
set -e

# test if dive is installed and make sure it is installed
if ! command -v dive &> /dev/null
then
    echo "dive could not be found, installing..."
    brew install dive
fi

docker build . -t neurodesktop:latest
dive neurodesktop --ci > wasted_space.txt
dive neurodesktop 

rm wasted_space.txt