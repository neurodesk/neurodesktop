#!/usr/bin/env bash
set -euo pipefail

failures=0

pass() {
    echo "[PASS] $1"
}

fail() {
    echo "[FAIL] $1"
    failures=$((failures + 1))
}

first_nonempty_line() {
    echo "$1" | sed '/^[[:space:]]*$/d' | head -n 1
}

check_cmd() {
    local label="$1"
    shift
    local output
    local line
    if output=$("$@" 2>&1); then
        line=$(first_nonempty_line "$output")
        pass "${label}: ${line:-ok}"
    else
        line=$(first_nonempty_line "$output")
        fail "${label}: ${line:-command failed}"
    fi
}

echo "Running Nextflow ecosystem smoke checks..."

check_cmd "nextflow" nextflow -version
check_cmd "nf-core" nf-core --version

if nf_test_output=$(nf-test --version 2>&1); then
    line=$(first_nonempty_line "$nf_test_output")
    pass "nf-test: ${line:-ok}"
elif nf_test_output=$(nf-test version 2>&1); then
    line=$(first_nonempty_line "$nf_test_output")
    pass "nf-test: ${line:-ok}"
else
    line=$(first_nonempty_line "$nf_test_output")
    fail "nf-test: ${line:-command failed}"
fi

NF_NEURO_MODULES_DIR="${NF_NEURO_MODULES_DIR:-/opt/nf-neuro/modules}"
if [ -d "${NF_NEURO_MODULES_DIR}/.git" ] || [ -f "${NF_NEURO_MODULES_DIR}/README.md" ]; then
    pass "nf-neuro modules checkout found at ${NF_NEURO_MODULES_DIR}"
else
    fail "nf-neuro modules checkout not found at ${NF_NEURO_MODULES_DIR}"
fi

if [ "$failures" -eq 0 ]; then
    echo "All checks passed."
else
    echo "${failures} check(s) failed."
    exit 1
fi
