# SPDX-License-Identifier: GPL-2.0-or-later
#
# The WP5 native toolchain: the WP0 native builder base plus exactly the
# packages locks/native-toolchain-packages.conf pins.
#
# Build and test only. It compiles the dedicated server and the native test
# client, runs that client for the packet census and carries the capture tool.
# Nothing built from this image's own files is distributed, and the distributed
# server image does not inherit it — that one starts from the separately pinned
# Debian runtime base.
#
# The base reference is passed in from locks/baseline.json rather than written
# here, so a digest can never be pinned in two places that drift apart.

ARG ARENA_NATIVE_BUILDER_BASE
FROM ${ARENA_NATIVE_BUILDER_BASE}

# The context is the verified package directory and nothing else:
# scripts/fetch-native-packages.sh has already rejected any file whose length
# or SHA-256 differs from the lock, and any file the lock does not pin.
COPY . /arena-packages

# Two passes, because `dpkg --install` unpacks in the order it is given and a
# Pre-Depends must already be configured at that point. Unpacking the whole
# closure first and then configuring it lets dpkg compute the real order. The
# forcing applies to the unpack pass only: `--configure -a` runs unforced, so
# an incomplete closure still fails here rather than producing a half-installed
# toolchain.
#
# The status check is anchored on purpose. `dpkg-query` prints
# "<package> <want> <error> <state>", and an unanchored match for
# "install ok installed" also matches "deinstall ok installed" — a removed
# package would read as a healthy one. The count check is the other half: a
# package that never unpacked at all leaves no status line to be unhealthy.
# Neither `$(...)` swallows a dpkg-query failure; only the `grep` no-match
# status is tolerated, which is what success looks like for the first one.
RUN set -eu; \
    export DEBIAN_FRONTEND=noninteractive; \
    expected="$(ls -1 /arena-packages/*.deb | wc -l)"; \
    dpkg --unpack --force-depends /arena-packages/*.deb; \
    dpkg --configure -a; \
    status="$(dpkg-query -W -f='${Package} ${Status}\n')"; \
    unconfigured="$(printf '%s\n' "${status}" | grep -v ' install ok installed$' || :)"; \
    if [ -n "${unconfigured}" ]; then \
      printf 'packages are not fully installed:\n%s\n' "${unconfigured}" >&2; \
      exit 1; \
    fi; \
    installed="$(printf '%s\n' "${status}" | grep -c ' install ok installed$' || :)"; \
    if [ "${installed}" -lt "${expected}" ]; then \
      printf 'only %s packages are installed, %s were staged\n' \
        "${installed}" "${expected}" >&2; \
      exit 1; \
    fi; \
    rm -rf /arena-packages; \
    rm -rf /var/lib/apt/lists/*; \
    cc --version | head -1; \
    cmake --version | head -1; \
    make --version | head -1

# An accepted build must not reach the network, and nothing in this image needs
# a package resolver. Leave the source list empty so a stray `apt-get update`
# inside the toolchain fails instead of quietly reaching a moving archive.
RUN rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list
