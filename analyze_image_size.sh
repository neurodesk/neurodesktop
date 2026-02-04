#!/bin/bash
set -e

# test if dive is installed and make sure it is installed
if ! command -v dive &> /dev/null
then
    echo "dive could not be found, installing..."
    brew install dive
fi

docker build . -t neurodesktop:latest

docker images neurodesktop:latest

# ask user to view layer history:
read -p "Do you want to view the image layer history? Default no (y/n) " -n 1 -r
echo    # move to a new line
if [[ $REPLY =~ ^[Yy]$ ]]
then
    docker history neurodesktop:latest
fi


# ask user to view layer history:
read -p "Do you want to run dive? Default no (y/n) " -n 1 -r
echo    # move to a new line
if [[ $REPLY =~ ^[Yy]$ ]]
then    
    # run dive to get a full analysis
    dive neurodesktop
fi