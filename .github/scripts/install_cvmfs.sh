#!/usr/bin/env bash

set -euo pipefail

release_package="$(mktemp --suffix=.deb)"
trap 'rm -f "$release_package"' EXIT

curl --fail --location --retry 3 \
  --output "$release_package" \
  https://ecsft.cern.ch/dist/cvmfs/cvmfs-release/cvmfs-release-latest_all.deb
sudo dpkg --install "$release_package"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install --yes \
  cvmfs \
  cvmfs-config-default

command -v cvmfs_config
