# SPDX-License-Identifier: GPL-2.0-or-later
#
# The arena-web dedicated server image.
#
# It starts from the separately pinned WP5 runtime base and deliberately **not**
# from the WP0 native builder: that image is recorded as build-only and must
# never be inherited by anything arena-web distributes. The server binary is
# built in the toolchain image against the builder's older glibc, which is why
# it runs on this newer one and not the other way around.
#
# Nothing is removed from the base. Its per-package /usr/share/doc/*/copyright
# files are the license evidence the baseline binds to this exact image, and
# `preserve-copyright-files` is a recorded redistribution obligation, so
# scripts/build-server-image.sh counts them in the built image and fails if any
# went missing.
#
# The base reference is passed in from locks/baseline.json rather than written
# here, so a digest can never be pinned in two places that drift apart.

ARG ARENA_SERVER_RUNTIME_BASE
FROM ${ARENA_SERVER_RUNTIME_BASE}

ARG ARENA_ENGINE_COMMIT
ARG ARENA_BASELINE_IDENTITY
ARG ARENA_PRODUCER_COMMIT

LABEL org.opencontainers.image.title="arena-web dedicated server" \
      org.opencontainers.image.licenses="GPL-2.0-or-later" \
      com.kaesual.arena-web.engine-commit="${ARENA_ENGINE_COMMIT}" \
      com.kaesual.arena-web.baseline-identity="${ARENA_BASELINE_IDENTITY}" \
      com.kaesual.arena-web.producer-commit="${ARENA_PRODUCER_COMMIT}"

# The build context is the staged tree and nothing else:
# scripts/build-server-image.sh has already refused any file whose SHA-256 or
# byte length differs from the committed manifests, and any file the profile
# does not declare.
#
# The staged tree already carries the deterministic modes scripts/arena_server.py
# sets — 0644 for every file, 0755 for every directory — so the game tree is
# copied without a --chmod, which would otherwise flatten its directories to a
# non-traversable mode as well.
COPY game /opt/arena-web
COPY --chmod=0755 ioq3ded /opt/arena-web/ioq3ded

# ioq3 code/sys/sys_main.c:838-839 sets the install path from DEFAULT_BASEDIR,
# which on Linux is the directory of the executable rather than the working
# directory (:739-747). fs_basepath is therefore /opt/arena-web and the
# standalone game directory resolves to /opt/arena-web/arena. The home path is
# derived from $HOME (code/sys/sys_unix.c), and the run mounts an empty tmpfs
# over it: the server writes its own configuration there and needs no writable
# state that outlives the container.
ENV HOME=/var/lib/arena
WORKDIR /opt/arena-web

# Nothing in the image needs to write outside that tmpfs, and nothing in it
# needs a privileged identity. 65534:65534 is the base's own nobody:nogroup.
USER 65534:65534

EXPOSE 27960/udp

ENTRYPOINT ["/opt/arena-web/ioq3ded"]
