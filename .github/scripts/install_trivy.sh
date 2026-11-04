#!/usr/bin/env bash
set -euo pipefail

# Keep the scanner version used by trivy-action v0.36.0, without its redirector.
version=0.70.0
case "$(uname -m)" in
    x86_64) arch=64bit ;;
    aarch64|arm64) arch=ARM64 ;;
    *) echo "Unsupported Trivy runner architecture" >&2; exit 1 ;;
esac
: "${RUNNER_TEMP:?}" "${GITHUB_PATH:?}"
work=$(mktemp -d "${RUNNER_TEMP}/trivy-download.XXXXXX")
trap 'rm -rf "$work"' EXIT
archive="trivy_${version}_Linux-${arch}.tar.gz"
checksums="trivy_${version}_checksums.txt"
release="https://github.com/aquasecurity/trivy/releases/download/v${version}"
for asset in "$archive" "$checksums"; do
    curl --fail --show-error --silent --location --retry 5 --retry-all-errors \
        --connect-timeout 20 --max-time 300 \
        "${release}/${asset}" --output "${work}/${asset}"
done
(
    cd "$work"
    awk -v archive="$archive" '$2 == archive { print; found = 1 } END { exit !found }' \
        "$checksums" > selected.sha256
    sha256sum --check --strict selected.sha256
    tar -xzf "$archive" trivy
)
install_dir="${RUNNER_TEMP}/neurodesktop-trivy"
mkdir -p "$install_dir"
install -m 755 "${work}/trivy" "${install_dir}/trivy"
"${install_dir}/trivy" --version
printf '%s\n' "$install_dir" >> "$GITHUB_PATH"
