#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 1 ]]; then
	echo "Usage: bash scripts/install_quality_tools.sh DESTINATION" >&2
	exit 2
fi
if [[ $(uname -s) != Linux ]]; then
	echo "Quality binary installation supports Linux only." >&2
	exit 2
fi
case "$(uname -m)" in
x86_64)
	architecture=amd64
	shellcheck_architecture=x86_64
	shellcheck_sha=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198
	actionlint_sha=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
	shfmt_sha=76e77641faa025814b77f153b29796b8e6fa2fca03e0c76a691608b86c7ea7bf
	;;
aarch64 | arm64)
	architecture=arm64
	shellcheck_architecture=aarch64
	shellcheck_sha=12b331c1d2db6b9eb13cfca64306b1b157a86eb69db83023e261eaa7e7c14588
	actionlint_sha=325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6
	shfmt_sha=5f2db09dae91fca848f7adbdd014632e921a383863a2ad7e0450ad3aba0c6489
	;;
*)
	echo "Unsupported Linux architecture: $(uname -m)" >&2
	exit 2
	;;
esac

mkdir -p "$1"
destination=$(cd "$1" && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

download() {
	local url=$1 digest=$2 output=$3
	curl --fail --location --retry 3 --silent --show-error "$url" --output "$output"
	printf '%s  %s\n' "$digest" "$output" | sha256sum --check --status
}

download "https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.${shellcheck_architecture}.tar.xz" "$shellcheck_sha" "$scratch/shellcheck.tar.xz"
download "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_${architecture}.tar.gz" "$actionlint_sha" "$scratch/actionlint.tar.gz"
download "https://github.com/mvdan/sh/releases/download/v3.14.1/shfmt_v3.14.1_linux_${architecture}" "$shfmt_sha" "$scratch/shfmt"
tar -xJf "$scratch/shellcheck.tar.xz" -C "$scratch" shellcheck-v0.11.0/shellcheck
tar -xzf "$scratch/actionlint.tar.gz" -C "$scratch" actionlint
install -m 0755 "$scratch/shellcheck-v0.11.0/shellcheck" "$destination/shellcheck"
install -m 0755 "$scratch/actionlint" "$destination/actionlint"
install -m 0755 "$scratch/shfmt" "$destination/shfmt"
printf 'Quality tools installed in %s; add this directory to PATH.\n' "$destination"
