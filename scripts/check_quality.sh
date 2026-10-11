#!/usr/bin/env bash
set -euo pipefail

QUALITY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$QUALITY_ROOT/scripts/check_quality.py" "$@"
