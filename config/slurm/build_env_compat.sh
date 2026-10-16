#!/usr/bin/env bash
set -euo pipefail

source_dir=$(realpath "$1")
destination=$(realpath -m "$2")
patch_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$source_dir"
printf '%s  %s\n' \
    5b96abe405d15833d941cd778b7fc5e0ce354a53ac3a290ae4959f64c7a7ec34 \
    src/common/env.c | sha256sum --check --strict
patch --batch --fuzz=0 -p1 < "$patch_dir/user-env-25.11.2.patch"
./configure --prefix=/usr --sysconfdir=/etc/slurm --without-munge \
    --disable-debug --disable-dependency-tracking SUCMD=/bin/su
install -d -m 0755 "$destination"
printf '{ global: env_array_user_default; local: *; };\n' > env.map
# Slurm exports error(); let the daemon resolve it without a glibc version tag.
gcc -shared -nostdlib -fPIC -O2 -fstack-protector-strong -ffunction-sections \
    -fdata-sections -I. src/common/env.c -Wl,--gc-sections \
    -Wl,-z,relro,-z,now -Wl,--version-script=env.map \
    -o "$destination/user-env.so"
chmod 0644 "$destination/user-env.so"
test "$(nm -D --defined-only "$destination/user-env.so" | awk '{print $3}')" = env_array_user_default
install -m 0644 COPYING "$destination/COPYING"
install -m 0644 "$patch_dir/user-env-25.11.2.patch" "$destination/"
