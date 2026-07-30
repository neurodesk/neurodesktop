#!/bin/bash
# Shared ~/.bashrc export reader.
#
# Coding-agent wrappers (opencode, codex, claude) and nbi_setup.sh all need the
# same operation: read the last "export VAR=value" line from ~/.bashrc, where
# the value may be single-quoted, double-quoted, or unquoted. That parsing rule
# was independently duplicated in each script as a near-identical sed triple;
# it is sourced from here so there is one owner for the bashrc contract.
#
# This file only defines readers. Each caller still owns its own variable's
# precedence (env var first), sanitization, and lifecycle (export), so the
# helper does not hide those differences — it only centralizes the parsing.

# Print the last value assigned to an "export VAR=value" line in ~/.bashrc.
# VAR_NAME is the variable name (without "export"). Returns the empty string
# when no line matches or the file is missing. The value is returned verbatim
# (including any quotes-as-content); callers sanitize before use.
read_export_from_bashrc() {
    local var_name="$1"
    local bashrc_file="${HOME}/.bashrc"

    [ -f "${bashrc_file}" ] || { printf ''; return; }

    sed -nE \
        -e "s/^[[:space:]]*export[[:space:]]+${var_name}='([^']+)'[[:space:]]*\$/\1/p" \
        -e "s/^[[:space:]]*export[[:space:]]+${var_name}=\"([^\"]+)\"[[:space:]]*\$/\1/p" \
        -e "s/^[[:space:]]*export[[:space:]]+${var_name}=([^[:space:]#]+)[[:space:]]*\$/\1/p" \
        "${bashrc_file}" | tail -n 1
}
