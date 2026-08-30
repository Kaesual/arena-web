# SPDX-License-Identifier: GPL-2.0-or-later
#
# Resolve and check the container runtime the WP5 native steps use. Sourced,
# never executed.
#
# These steps are Podman-specific and say so rather than failing obscurely half
# way through a build. Two constructs have no Docker equivalent:
#
#   podman build --timestamp   fixes every layer's mtime, which is what makes
#                              two assemblies of the same content produce the
#                              same image id rather than two images that differ
#                              only in when they were made;
#   podman image exists        a plain existence test that does not print an
#                              error or require parsing `inspect` output.
#
# `--pull=never` on `build` is spelled differently by Docker's BuildKit as well.
# The reproducible-image claim in docs/wp5-packet-census.md is therefore a
# Podman claim, and this guard keeps the scripts honest about it.

arena_container_runtime() {
  printf '%s\n' "${CONTAINER_RUNTIME:-podman}"
}

arena_require_container_runtime() {
  local runtime="$1"
  if ! command -v "${runtime}" >/dev/null 2>&1; then
    printf 'the container runtime %s is not installed\n' "${runtime}" >&2
    return 1
  fi
  local missing=()
  if ! "${runtime}" image exists --help >/dev/null 2>&1; then
    missing+=("image exists")
  fi
  if ! "${runtime}" build --help 2>/dev/null | grep -q -- '--timestamp'; then
    missing+=("build --timestamp")
  fi
  if [[ ${#missing[@]} -ne 0 ]]; then
    printf 'the container runtime %s does not support: %s\n' \
      "${runtime}" "${missing[*]}" >&2
    printf 'the WP5 native steps need Podman; set CONTAINER_RUNTIME to a\n' >&2
    printf 'podman-compatible runtime, or leave it unset to use podman.\n' >&2
    return 1
  fi
  return 0
}
