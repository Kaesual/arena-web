#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Serve the checkout on loopback so the relay conformance probe can be opened in
# the browser. The probe reads locks/relay-measurement-vector.json as well as its
# own files, so the repository root is the served directory.
#
# This server is for local use only. It hands out no configuration: the endpoint,
# trust input, authorization and routing prefix are typed into the page at
# runtime and never leave the browser.

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host="127.0.0.1"
port="8173"

usage() {
  printf 'usage: %s [--port PORT]\n' "$0" >&2
}

case "${1:-}" in
  "")
    ;;
  --port)
    port="${2:-}"
    if ! [[ "${port}" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
      printf 'invalid port: %s\n' "${port}" >&2
      exit 2
    fi
    ;;
  *)
    usage
    exit 2
    ;;
esac

printf 'serving %s\n' "${repo_dir}"
printf 'probe:   http://%s:%s/probe/\n' "${host}" "${port}"
# http://127.0.0.1 is a secure context, so WebTransport is available without a
# certificate for the page itself.
exec python3 -m http.server --bind "${host}" --directory "${repo_dir}" "${port}"
