#!/usr/bin/env bash

# Reduce a JupyterHub user model to the state of the user's default server.
#
# Reads the JSON body of GET /hub/api/users/<user> on stdin and prints one of:
#
#   ready           the default server is up and answering
#   pending:<what>  the Hub is still spawning or stopping it (pending:spawn, ...)
#   starting        the Hub has a server URL but has not marked it ready
#   stopped         there is no default server
#
# The Hub always includes the top-level "server" key, as null when nothing is
# running, so a stopped server cannot be detected by the key disappearing; the
# nightly probe waited out its full cleanup timeout on every run for that
# reason (neurodesk/neurodesktop#990). The default server's own state lives
# under servers[""] when the token may read it, and the top-level "server" and
# "pending" fields are the fallback.
#
# Exits 1 without printing a state when the body is not a user model, such as
# an error object for an invalid token.
#
# Usage:
#   curl ... /hub/api/users/<user> | jupyterhub_server_state.sh

set -uo pipefail

jq -er '
    if type != "object" or (has("name") | not) then
        error("not a JupyterHub user model")
    else
        (if (.servers | type) == "object" and (.servers[""] | type) == "object"
         then .servers[""] else null end) as $default
        | ($default.pending // .pending) as $pending
        | ($default.url // .server) as $url
        | if $pending != null then "pending:\($pending)"
          elif ($default != null and $default.ready == true)
            or ($default == null and $url != null) then "ready"
          elif $url != null then "starting"
          else "stopped"
          end
    end
' 2>/dev/null || exit 1
