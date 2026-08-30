#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Runs inside the WP5 native toolchain image and starts the native test client
# on a virtual X server. It is never meant to be run on a host:
# scripts/census_run.py supplies the mounts, the display size and the client's
# command line, and drives the client's console over this process's stdin.
#
# A real GL context is required. SDL's dummy video driver provides none, so the
# client would fail renderer initialisation; Xvfb plus Mesa's software rasteriser
# gives a context without needing a GPU or a seat.

set -eu

: "${ARENA_DISPLAY:?display was not supplied}"
: "${ARENA_SCREEN:?screen geometry was not supplied}"
: "${ARENA_CLIENT_BINARY:?client binary was not supplied}"

Xvfb "${ARENA_DISPLAY}" -screen 0 "${ARENA_SCREEN}" -nolisten tcp &
xvfb_pid=$!

# Wait for the display socket rather than sleeping a guessed amount.
socket="/tmp/.X11-unix/X${ARENA_DISPLAY#:}"
waited=0
while [ ! -e "${socket}" ]; do
  if ! kill -0 "${xvfb_pid}" 2>/dev/null; then
    printf 'the virtual X server exited before it was ready\n' >&2
    exit 1
  fi
  if [ "${waited}" -ge 200 ]; then
    printf 'the virtual X server did not become ready\n' >&2
    kill "${xvfb_pid}" 2>/dev/null || true
    exit 1
  fi
  waited=$((waited + 1))
  sleep 0.05
done

export DISPLAY="${ARENA_DISPLAY}"
# Mesa's software rasteriser, chosen explicitly rather than left to whatever a
# host driver might offer inside the container.
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe

# The client inherits this process's stdin, which is how the census drives its
# console (ioq3 code/sys/con_tty.c falls back to plain stdin reads when stdin is
# not a terminal, and code/qcommon/common.c Com_GetEvent queues what it reads).
"${ARENA_CLIENT_BINARY}" "$@"
status=$?

kill "${xvfb_pid}" 2>/dev/null || true
wait "${xvfb_pid}" 2>/dev/null || true
exit "${status}"
