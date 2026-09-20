#!/usr/bin/env bash
# Source before starting a test container so errexit cannot bypass cleanup.
cleanup_image_test() {
  local image_test_status=$?
  trap - EXIT
  if ! docker rm -f neurodesktop-test >/dev/null 2>&1; then
    echo '::warning::Could not remove the image test container.' >&2
    if [ "$image_test_status" -eq 0 ]; then image_test_status=1; fi
  fi
  if [ -n "${HPC_HOME_DIR:-}" ]; then
    if ! docker run --rm --user 0:0 --entrypoint chown \
      -v "$HPC_HOME_DIR":/cleanup "$IMAGE_REF" \
      -R "$(id -u):$(id -g)" /cleanup >/dev/null 2>&1; then
      echo '::warning::Could not restore ownership of the HPC test home.' >&2
      if [ "$image_test_status" -eq 0 ]; then image_test_status=1; fi
    fi
    if ! rm -rf -- "$HPC_HOME_DIR" "${HPC_PASSWD_FILE:-}" "${HPC_GROUP_FILE:-}"; then
      echo '::warning::Could not remove HPC test files.' >&2
      if [ "$image_test_status" -eq 0 ]; then image_test_status=1; fi
    fi
  fi
  exit "$image_test_status"
}
trap cleanup_image_test EXIT
