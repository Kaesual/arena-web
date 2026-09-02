# SPDX-License-Identifier: GPL-2.0-or-later
"""The served runtime set of the browser vertical slice.

This module owns the packaging discipline of WP4. It answers exactly one
question — which bytes may a local serve hand to the browser — and it answers
it fail-closed:

* the content configuration ``arena/game-profile.json`` is validated against
  its own rules and cross-checked against ``content/pack-recipe.json``, so the
  loader cannot be pointed at a map, player model, frag limit or bot the
  audited content pack does not contain;
* the engine arguments committed in that configuration must be exactly the
  derivation of its declarative fields, so the list the loader executes cannot
  drift away from the profile it claims to start;
* every artifact is copied out of a build directory only after its SHA-256 and
  byte length match the committed manifest entry, and the staged tree is then
  re-read and compared to the expected file set, so neither an extra nor a
  missing nor a modified file can reach the browser.

Nothing here is heavy and nothing here is committed: the staged tree lives
under the gitignored ``build/`` directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

CHUNK_SIZE = 1024 * 1024

PROFILE_SOURCE = "arena/game-profile.json"
PROFILE_SERVED = "game-profile.json"
LOADER_SOURCE_DIRECTORY = "arena"
RUNTIME_SOURCE_FILES = {
    "index.html": "arena/index.html",
    "loader.js": "arena/loader.js",
    "arena/canvas-resize.js": "arena/canvas-resize.js",
    "arena/host-lifecycle.js": "arena/host-lifecycle.js",
    "arena/network-backend.js": "arena/network-backend.js",
    "arena/relay-profile.json": "arena/relay-profile.json",
    "probe/relay-framing.js": "probe/relay-framing.js",
}
LOADER_FILES = tuple(RUNTIME_SOURCE_FILES)

PROFILE_KEYS = (
    "$comment",
    "artifacts",
    "basegame",
    "bots",
    "configFiles",
    "cvarNotes",
    "cvars",
    "engineArguments",
    "engineCommandLine",
    "engineCommandLineNotes",
    "formatVersion",
    "manifests",
    "package",
    "playerModel",
    "readyMarkerNotes",
    "readyMarkers",
)

# ioq3 code/qcommon/files.c: FS_CheckPak0 leaves com_standalone at 0 whenever
# com_basegame is ioquake3's own retail game directory, and the engine then
# refuses to start without pak0..pak8. Any other name selects standalone
# operation, which is what a game built on this engine is.
IOQ3_RETAIL_BASEGAME = "baseq3"

# ioq3 code/qcommon/files.c: FS_InitFilesystem stops with a fatal error unless
# the active game directory has a readable default.cfg. It is an engine
# requirement rather than a game-module reference, so the audited content pack
# — the closure of what the pinned QVM sources name — does not carry one and
# the profile has to supply it.
REQUIRED_CONFIG_FILE = "default.cfg"

MANIFEST_NAMES = ("content", "engine")
MANIFEST_PREFIXES = {"content": "content", "engine": "engine"}

# Where the per-map recipe fragments live, and the content-manifest input id
# under which each one enters the release identity.
MAP_FRAGMENT_DIRECTORY = "content/maps"
MAP_FRAGMENT_INPUT_PREFIX = "arena-web-map-"

# Content archives are served under an immutable cache policy, so their names
# carry their own digest and a published name is never rewritten. This many
# hex characters of the artifact's own SHA-256: 64 bits, which is plenty to
# separate the versions of one archive and short enough to read.
SERVED_DIGEST_PREFIX_LENGTH = 16

# Which manifests' artifacts are served under a hashed name. Engine artifacts
# are not: they are fetched once per release beside the profile that names
# them, and renaming them would move `manifests/browser-client.json`'s own
# artifact paths, which the browser build writes.
HASHED_SERVED_MANIFESTS = ("content",)

# The per-artifact records the content manifest carries beyond an artifact's
# own identity. `map` is the selection key a rotation is expressed in;
# `uncompressedSize` and `peakHunkBytes` are what a caller needs in order to
# budget one. They exist on content artifacts only — an engine artifact has no
# map, and neither manifest may borrow the other's vocabulary.
CONTENT_ARTIFACT_RECORDS = ("map", "peakHunkBytes", "uncompressedSize")

# The pinned engine tree, and the header the systeminfo bound is read out of.
ENGINE_ROOT = "ioq3"
SYSTEMINFO_HEADER = "code/qcommon/q_shared.h"

# ioq3 code/qcommon/files.c FS_ReferencedPakChecksums writes `va("%i ", ...)`
# per referenced pack, and the checksum is a signed 32-bit int: eleven
# characters at worst, plus the trailing space it always emits.
SYSTEMINFO_CHECKSUM_WIDTH = len("-2147483648") + 1

# Everything in CS_SYSTEMINFO that is not one of the two referenced-pak keys.
# Measured on the pinned engine and QVMs by running the dedicated server with
# the published archive set and reading `systeminfo` back: thirteen cvars,
# 153 bytes, of which 122 were the two pak keys.
#
# A remembered measurement would be the one number in this bound that nothing
# checks, so it is not left as one: `systeminfo_fixed_floor` enumerates the
# CVAR_SYSTEMINFO registrations of the pinned sources and this allowance must
# cover them. The enumeration reproduces exactly the thirteen cvars the real
# server printed, plus the client-only `cl_anonymous` that a listen server adds.
SYSTEMINFO_FIXED_ALLOWANCE = 512

# How much of that allowance each non-pak cvar may spend on its *value*. The
# widest one a run can produce is `sv_serverid`, an int printed with `%i`
# (11 characters at worst); `sv_voipProtocol` is "opus" and the rest are single
# digits or empty. Sixteen is that with room, per cvar.
SYSTEMINFO_VALUE_ALLOWANCE = 16

# ioq3 code/server/sv_init.c: the two keys that grow with the archive set.
SYSTEMINFO_PAK_KEYS = ("sv_referencedPakNames", "sv_referencedPaks")

# The two shapes a CVAR_SYSTEMINFO cvar is registered in: the engine's
# `Cvar_Get("name", ..., ... CVAR_SYSTEMINFO ...)` and the game modules' table
# rows `{ &var, "name", "default", CVAR_SYSTEMINFO, ... }`. Both require a
# quoted name in the same statement, so a line that merely tests the flag —
# `cvar_modifiedFlags & CVAR_SYSTEMINFO` — is not a registration.
SYSTEMINFO_CVAR_PATTERNS = (
    re.compile(
        r'Cvar_Get\s*\(\s*"([A-Za-z_][A-Za-z0-9_]*)"[^;]*?CVAR_SYSTEMINFO', re.S
    ),
    re.compile(
        r'\{\s*&[A-Za-z_][A-Za-z0-9_]*\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"'
        r"[^}]*?CVAR_SYSTEMINFO",
        re.S,
    ),
)

# The rotation, and the two ceilings it runs into.
#
# ioq3 code/game/g_main.c ExitLevel: when a level ends the gamecode executes
# `vstr nextmap`, and `nextmap` is an ordinary cvar. Stock baseq3 carries no map
# list of its own, so a rotation is id's own idiom -- a cycle of `d<N>` cvars,
# each of which loads its map and points `nextmap` at the next one. The cycle is
# started with `vstr d1` rather than with `+map <first>`, so that ExitLevel's
# `nextmap == "map_restart 0"` special case (g_main.c) is not on the automatic
# path: every spawn a `d<N>` step performs is immediately followed by that
# step's own `set nextmap`, and `SV_SpawnServer` is synchronous inside the
# `map` command, so the value at the next level end is never the engine's
# default.
#
# That is a statement about the automatic path and not an unconditional one.
# `SV_MapRestart_f` calls `SV_SpawnServer` directly when `sv_maxclients` or
# `sv_gametype` is modified (ioq3 code/server/sv_ccmds.c), with nothing
# restoring `nextmap` afterwards, and both `callvote g_gametype` and
# `callvote map_restart` are permitted votes with `g_allowVote` at its default
# of 1. After that sequence the special case does fire at the next level end —
# bounded, because it runs `vstr d1` and the cycle resumes from its first
# entry, but the rotation has then skipped back to the top rather than
# continued. `callvote map <x>` is safe: the gamecode rebuilds that vote as
# `map <x>; set nextmap "<current>"` (code/game/g_cmds.c).
ROTATION_CVAR = "d{index}"
ROTATION_STEP = "map {map};set nextmap vstr {next}"
ROTATION_START = "d1"

# ioq3 code/sys/sys_main.c main(): argv is concatenated into one command line,
# an argument containing a space is wrapped in quotes, and each is followed by a
# space. The buffer is fixed and `Q_strcat` reaches it through `Q_strncpyz`,
# which truncates rather than failing (code/qcommon/q_shared.c), so an
# overlong list loses its tail in silence.
COMMAND_LINE_SOURCE = "code/sys/sys_main.c"
COMMAND_LINE_BUFFER = re.compile(
    r"^\s*char\s+commandLine\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]", re.M
)
COMMAND_LINE_LIMIT_HEADER = "code/qcommon/q_shared.h"

# ioq3 code/qcommon/common.c Com_ParseCommandLine: the command line starts as
# one console line and every unquoted '+' begins another, but the function
# *returns* once it holds MAX_CONSOLE_LINES of them -- so arguments past that
# point are not truncated so much as glued to the last line, and neither
# outcome is reported.
CONSOLE_LINE_SOURCE = "code/qcommon/common.c"
CONSOLE_LINE_LIMIT = "MAX_CONSOLE_LINES"

# The committed, engine-derived bound each profile carries so a consumer can
# read it without running this module, and the keys it holds.
COMMAND_LINE_LIMIT_KEYS = ("maxBytes", "maxLines")

# The one placeholder a ready marker may carry. The map is a launch argument, so
# the marker that names it is a template rather than a literal.
MARKER_MAP_PLACEHOLDER = "{map}"
SERVER_SPAWNED_MARKER = f"Server: {MARKER_MAP_PLACEHOLDER}"

# ioq3 code/game/g_main.c CheckExitRules: a level ends on a limit cvar, and
# `vstr nextmap` runs only when it ends. Which limits those are is read out of
# the pinned gamecode rather than restated here — see `match_end_limits` — and
# these are the ones this profile's gametype can reach.
MATCH_END_SOURCE = "code/game/g_main.c"
MATCH_END_FUNCTION = "void CheckExitRules( void ) {"
MATCH_END_LIMIT = re.compile(r"\bg_([a-z]+limit)\.integer\b")
MATCH_END_CVARS = ("fraglimit", "timelimit")

# A limit `CheckExitRules` reads that this profile cannot reach, with the engine
# site that decides it. An entry is a claim about reachability, not a way to
# skip covering a limit: if the gamecode grows another one it is neither covered
# nor exempted, and this fails rather than silently checking two rules of three.
MATCH_END_UNREACHABLE = {
    "capturelimit": "guarded by g_gametype.integer >= GT_CTF, and this profile "
    "is GT_FFA",
}

# The arena `type` vocabulary this release supports. Upstream types carry the
# OpenArena-only tags whose game code this product does not ship (`lms`,
# `elimination`, `dom`, `dd`, `harvester`), so a fragment normalises rather than
# copies; what survives is a space-separated set drawn from these two.
SUPPORTED_ARENA_TYPES = ("ffa", "tourney")

# What every published map declares, and the only thing it may declare. The map
# is a launch argument now, so a rotation may reach any archive the release
# publishes, and the committed gametype is GT_FFA on both profiles — so "the
# started arena is an FFA arena" stops being a statement about one map and
# becomes one about the set. `tourney` stays in the vocabulary above because it
# is what upstream may say and what the normalisation drops, not because a
# fragment may carry it: the reduction rule is `ffa`, always
# (docs/wp3-content-closure.md, "A fragment's arena `type` is `ffa`, always").
# Checking membership rather than equality left that rule with no gate at all,
# which is how it came to be documented as something else.
PUBLISHED_ARENA_TYPE = "ffa"

DECIMAL = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")


# "clientEnteredGame" is deliberately not a bot marker: ioq3
# code/game/g_client.c:1026 prints it for every client that begins, the local
# player before any bot. Bots are proved by name, from profile.bots.
READY_MARKER_NAMES = ("clientEnteredGame", "clientGameLoaded", "serverSpawned")

ARTIFACT_ROLES = ("filesystem", "module-script", "module-wasm")
SINGLETON_ROLES = ("module-script", "module-wasm")

# The engine build also emits ioquake3's generated demo shell, its retail-data
# configuration and the missionpack QVMs. WP1 records the first two as build
# evidence and the third as an upstream by-product; none of them is a product
# input, so the served set may never contain one even if a profile asked for it.
FORBIDDEN_ARTIFACT_PATHS = ("ioquake3-config.json", "ioquake3.html")
FORBIDDEN_ARTIFACT_PREFIXES = ("missionpack/",)

# The content pack carries no gamecode and the engine build emits no game data.
FORBIDDEN_SUFFIXES_BY_MANIFEST = {"content": (".qvm",), "engine": (".pk3",)}

CVAR_NAME = re.compile(r"\A[a-z][A-Za-z0-9_]*\Z")
# A game-directory name, which is this product's own and stays narrow.
BASEGAME_NAME = re.compile(r"\A[a-z0-9][a-z0-9_]*\Z")
# A map name, which is upstream's and is not. `content_pack.MAP_FRAGMENT_NAME`
# has always allowed the hyphen that `pul1duel-oa` — already audited and
# scheduled for a later batch — carries; this copy did not, so a map that the
# content build accepts would have been refused one authority further on. The
# two grammars are the same grammar and are written the same way.
MAP_NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]*\Z")
BOT_NAME = re.compile(r"\A[A-Za-z][A-Za-z0-9_]*\Z")
GAME_PATH = re.compile(
    r"\A[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*\Z"
)
SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")

# ioq3 code/game/g_bot.c: G_SpawnBots hands addbot a base delay of
# BOT_BEGIN_DELAY_BASE and adds BOT_BEGIN_DELAY_INCREMENT per further bot.
BOT_BEGIN_DELAY_BASE_MS = 2000
BOT_BEGIN_DELAY_INCREMENT_MS = 1500

# ioq3 code/game/g_bot.c: Svcmd_AddBot_f clamps the skill to this range.
BOT_SKILL_RANGE = (1, 5)

# ioq3 code/game/bg_public.h: GT_FFA.
FFA_GAMETYPE = "0"

# The loader derives these from the live canvas box; a committed value would be
# an environment-specific one.
RUNTIME_DERIVED_CVARS = ("r_customheight", "r_customwidth", "r_mode")

RELAY_PROFILE_SOURCE = "arena/relay-profile.json"
RELAY_PROFILE_KEYS = (
    "$comment",
    "connectFamily",
    "cvars",
    "formatVersion",
    "fragmentSize",
    "innerDatagramFloor",
    "keepAliveIntervalSource",
    "mode",
    "receiveQueueDepth",
    "singleDatagramOverhead",
)
RELAY_PROFILE_CVARS = {
    "bot_enable": "0",
    "cl_allowDownload": "0",
    "cl_motd": "0",
    "cl_voip": "0",
    "com_basegame": "arena",
    "com_legacyprotocol": "0",
    "headmodel": "skelebot/default",
    "model": "skelebot/default",
    "net_enabled": "2",
    "r_allowResize": "1",
    "r_fullscreen": "0",
    "sv_pure": "0",
}


class ArenaRuntimeError(ValueError):
    """A runtime set that may not be served."""


def _fail(path: str, message: str) -> None:
    raise ArenaRuntimeError(f"{path}: {message}")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(path, "must be a non-empty string")
    return value


def _exact_keys(value: dict[str, Any], expected: tuple[str, ...], path: str) -> None:
    actual = tuple(sorted(value))
    if actual != tuple(sorted(expected)):
        missing = sorted(set(expected) - set(actual))
        unknown = sorted(set(actual) - set(expected))
        _fail(path, f"unexpected key set (missing {missing}, unknown {unknown})")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, what: str) -> Any:
    if not path.is_file():
        _fail(what, f"{path} does not exist")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:  # pragma: no cover - message passthrough
        _fail(what, f"is not valid JSON: {error}")
    return None


def manifest_index(manifest: Any, what: str) -> dict[str, dict[str, Any]]:
    """Return ``path -> {sha256, size}`` of an artifact manifest."""
    record = _object(manifest, what)
    if record.get("digestAlgorithm") != "sha256":
        _fail(what, "digestAlgorithm must be 'sha256'")
    index: dict[str, dict[str, Any]] = {}
    for entry in _array(record.get("artifacts"), f"{what}.artifacts"):
        artifact = _object(entry, f"{what}.artifacts entry")
        artifact_path = _string(artifact.get("path"), f"{what}.artifacts[].path")
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            _fail(
                f"{what}.artifacts[{artifact_path}].sha256", "must be a SHA-256 digest"
            )
        size = artifact.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _fail(
                f"{what}.artifacts[{artifact_path}].size",
                "must be a non-negative integer",
            )
        if artifact_path in index:
            _fail(f"{what}.artifacts", f"declares '{artifact_path}' twice")
        entry = {"sha256": digest, "size": size}
        for name in CONTENT_ARTIFACT_RECORDS:
            if name in artifact:
                entry[name] = artifact[name]
        index[artifact_path] = entry
    if not index:
        _fail(f"{what}.artifacts", "is empty")
    return index


def content_archive_names(profile: dict[str, Any]) -> list[str]:
    """The archive names the engine sees, as `pakBasename` holds them.

    ioq3 code/qcommon/files.c: FS_LoadZipFile strips `.pk3` from the file name,
    so the name the engine puts into `sv_referencedPakNames` is the manifest
    basename without its suffix — never the served name, whose digest is cache
    addressing the engine never sees. Returned sorted for a stable projection;
    the order does not affect its length.
    """
    names = []
    for artifact in profile["artifacts"]:
        if artifact["manifest"] != "content":
            continue
        base = artifact["path"].rsplit("/", 1)[-1]
        names.append(base[: -len(".pk3")] if base.endswith(".pk3") else base)
    return sorted(names)


def projected_systeminfo_size(basegame: str, archive_names: Iterable[str]) -> int:
    """How large CS_SYSTEMINFO becomes if every named archive is referenced.

    `SV_SpawnServer` assembles the whole systeminfo with
    `Cvar_InfoString_Big`, whose buffer is `BIG_INFO_STRING`, and
    `Info_SetValueForKey_Big` neither truncates nor fails on overflow: it
    prints one line and **returns**, leaving the key out (ioq3
    code/qcommon/q_shared.c). Which key falls out is whichever one first does
    not fit, and `Cvar_InfoString_Big` walks `cvar_vars` in list order, so it
    is not even predictably a pak key. Silent and undeterministic in what it
    hits, which is why this is projected rather than hoped for.

    The projection is deliberately the *pessimistic* one. Measured, a
    dedicated server references two archives — `FS_ClearPakReferences(0)` runs
    on every `SV_SpawnServer` (code/server/sv_init.c) and only the base and
    the loaded map are touched afterwards — but the bound must not depend on
    which files a session happens to open, so every published archive is
    counted. Any rotation is a subset of the published set and the string
    grows monotonically with it, so a set that fits leaves every rotation of
    it fitting too.
    """
    names = list(archive_names)
    # FS_ReferencedPakNames joins "<gamename>/<basename>" with single spaces;
    # FS_ReferencedPakChecksums emits one "%i " per pack, trailing space
    # included.
    values = {
        "sv_referencedPakNames": len(
            " ".join(f"{basegame}/{name}" for name in names)
        ),
        "sv_referencedPaks": SYSTEMINFO_CHECKSUM_WIDTH * len(names),
    }
    # Info_SetValueForKey_Big appends a literal backslash-key-backslash-value
    # per cvar, so each key costs its own name plus two separators.
    pak_keys = sum(1 + len(key) + 1 + values[key] for key in SYSTEMINFO_PAK_KEYS)
    return SYSTEMINFO_FIXED_ALLOWANCE + pak_keys


def systeminfo_cvars(engine_root: Path) -> list[str]:
    """Every CVAR_SYSTEMINFO cvar the pinned sources register, by name."""
    names: set[str] = set()
    sources = sorted((engine_root / "code").rglob("*.c"))
    if not sources:
        _fail(
            "profile.artifacts",
            f"{engine_root}/code holds no sources, so the systeminfo cvar set "
            "cannot be read out of the pinned engine",
        )
    for path in sources:
        text = path.read_text(encoding="latin-1")
        for pattern in SYSTEMINFO_CVAR_PATTERNS:
            names.update(match.group(1) for match in pattern.finditer(text))
    return sorted(names)


def systeminfo_fixed_floor(engine_root: Path) -> int:
    """The least CS_SYSTEMINFO the pinned sources can produce beside the paks.

    `SYSTEMINFO_FIXED_ALLOWANCE` is a measurement, and a measurement nothing
    re-derives is the one term of a bound that can go quietly wrong. This is
    what re-derives it: the cvars are enumerated from the pinned sources, so a
    gamecode or engine that registers another CVAR_SYSTEMINFO cvar has to fit
    the allowance or fail here rather than eat into the pak headroom unnoticed.
    """
    fixed = [name for name in systeminfo_cvars(engine_root) if name not in SYSTEMINFO_PAK_KEYS]
    if not fixed:
        _fail(
            "profile.artifacts",
            "the pinned sources register no non-pak CVAR_SYSTEMINFO cvar, so the "
            "fixed part of the projection would be satisfied by measuring nothing",
        )
    return sum(1 + len(name) + 1 + SYSTEMINFO_VALUE_ALLOWANCE for name in fixed)


def check_systeminfo_budget(repo_root: Path, profile: dict[str, Any]) -> int:
    """Refuse a published archive set that could overflow CS_SYSTEMINFO."""
    from content_pack import ContentError, engine_constant

    engine_root = repo_root / ENGINE_ROOT
    try:
        limit = engine_constant(engine_root, "BIG_INFO_STRING", SYSTEMINFO_HEADER)
    except ContentError as error:
        _fail("profile.artifacts", str(error))
    floor = systeminfo_fixed_floor(engine_root)
    if floor > SYSTEMINFO_FIXED_ALLOWANCE:
        _fail(
            "profile.artifacts",
            f"the pinned sources' CVAR_SYSTEMINFO cvars need at least {floor} bytes "
            f"beside the referenced-pak keys, and the projection allows "
            f"{SYSTEMINFO_FIXED_ALLOWANCE}",
        )
    names = content_archive_names(profile)
    projected = projected_systeminfo_size(profile["basegame"], names)
    if projected >= limit:
        _fail(
            "profile.artifacts",
            f"the {len(names)} published content archives project a "
            f"{projected}-byte CS_SYSTEMINFO against the engine's "
            f"BIG_INFO_STRING of {limit}; the overflow drops a systeminfo key "
            "without an error (ioq3 code/qcommon/q_shared.c "
            "Info_SetValueForKey_Big)",
        )
    return projected


def engine_command_line_limits(engine_root: Path) -> dict[str, int]:
    """The two silent ceilings a launch argument list runs into, read from the pin.

    Neither is restated here. ``MAX_CONSOLE_LINES`` is read from the file that
    defines it, and the byte bound is read *through* the declaration that uses
    it: ``sys_main.c`` sizes its command-line buffer with a named constant, so
    the name is taken from that declaration and only then resolved in the
    header. Hard-coding either would make this gate a copy of a number rather
    than a reading of the engine, and an engine pin that shrank a buffer would
    leave it permissive.
    """
    from content_pack import ContentError, engine_constant

    source = engine_root / COMMAND_LINE_SOURCE
    try:
        text = source.read_text(encoding="latin-1")
    except OSError as error:
        _fail(
            "engine command line",
            f"cannot read {COMMAND_LINE_SOURCE} out of the pinned engine tree at "
            f"{engine_root}: {error}",
        )
    match = COMMAND_LINE_BUFFER.search(text)
    if match is None:
        _fail(
            "engine command line",
            f"{COMMAND_LINE_SOURCE} no longer declares a fixed `char commandLine[]`, "
            "so the byte bound of a launch argument list cannot be read out of the "
            "pinned engine",
        )
    try:
        return {
            "maxBytes": engine_constant(
                engine_root, match.group(1), COMMAND_LINE_LIMIT_HEADER
            ),
            "maxLines": engine_constant(
                engine_root, CONSOLE_LINE_LIMIT, CONSOLE_LINE_SOURCE
            ),
        }
    except ContentError as error:
        _fail("engine command line", str(error))
    return {}


def engine_command_line(arguments: Iterable[str]) -> str:
    """The exact string ioq3 assembles from argv (code/sys/sys_main.c main())."""
    return "".join(
        f'"{argument}" ' if " " in argument else f"{argument} "
        for argument in arguments
    )


def engine_console_lines(arguments: Iterable[str]) -> int:
    """How many console lines that string becomes (Com_ParseCommandLine).

    The whole line is one console line to begin with, and each '+' outside a
    quoted section starts another. Quoting is tracked because a '+' inside a
    rotation step's value must not count — and a newline or carriage return
    breaks a line *regardless* of quoting, which is modelled here even though
    nothing this repository generates can contain one: a counter that is exact
    only because of a property of its inputs is a counter that stops being
    exact when the inputs change.
    """
    lines = 1
    quoted = False
    for character in engine_command_line(arguments):
        if character == '"':
            quoted = not quoted
        elif character in ("\n", "\r") or (character == "+" and not quoted):
            lines += 1
    return lines


def check_command_line_budget(
    arguments: list[str], limits: dict[str, int], what: str
) -> None:
    """Refuse an argument list the engine would silently cut down.

    Both failures are quiet: `Q_strcat` reaches a full buffer through
    `Q_strncpyz` and simply stops copying, and `Com_ParseCommandLine` returns
    once it holds `MAX_CONSOLE_LINES`, leaving everything after that point
    attached to the last line it took. A list that does not fit therefore
    starts a server that is subtly not the one that was asked for, which is
    exactly the class of failure this repository refuses to ship unguarded.
    """
    size = len(engine_command_line(arguments))
    if size >= limits["maxBytes"]:
        _fail(
            what,
            f"assembles a {size}-byte engine command line against the pinned "
            f"engine's {limits['maxBytes']}-byte buffer; the overflow is "
            "truncated in silence (ioq3 code/sys/sys_main.c, "
            "code/qcommon/q_shared.c Q_strcat)",
        )
    lines = engine_console_lines(arguments)
    if lines > limits["maxLines"]:
        _fail(
            what,
            f"becomes {lines} console lines against the pinned engine's "
            f"{limits['maxLines']}; Com_ParseCommandLine returns at that count "
            "and the rest is neither parsed nor reported (ioq3 "
            "code/qcommon/common.c)",
        )


def check_command_line_limits(
    repo_root: Path, committed: Any, what: str
) -> dict[str, int]:
    """Bind a profile's published bound to the engine it claims to describe."""
    record = _object(committed, what)
    _exact_keys(record, COMMAND_LINE_LIMIT_KEYS, what)
    limits = engine_command_line_limits(repo_root / ENGINE_ROOT)
    for name in COMMAND_LINE_LIMIT_KEYS:
        value = record.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            _fail(f"{what}.{name}", "must be an integer")
        if value != limits[name]:
            _fail(
                f"{what}.{name}",
                f"is {value}, and the pinned engine's is {limits[name]}",
            )
    return limits


def validate_rotation(rotation: Any, published: Iterable[str], what: str) -> list[str]:
    """The rotation as a list: ordered, repeats allowed, never empty.

    Order and repetition are kept because they are the rotation — the server
    plays the list as written and a cycle may legitimately visit one map twice.
    The client's fetch set is the same list canonicalised, which is the loader's
    job; the two derivations differ in exactly that and in nothing else.

    Refusing an empty list is the server-side half of the rule the loader
    enforces for the browser: this repository cannot check that a caller's two
    derivations came from one list (it holds neither input), but it can refuse
    to produce a command line for a rotation nobody chose.
    """
    names = _array(rotation, what)
    if not names:
        _fail(
            what,
            "is empty; the map a server plays is a launch argument and there is "
            "no default, because the only plausible one is a server playing a "
            "map its clients were never told to fetch",
        )
    known = set(published)
    for index, name in enumerate(names):
        entry = _string(name, f"{what}[{index}]")
        if not MAP_NAME.fullmatch(entry):
            _fail(f"{what}[{index}]", f"'{entry}' is not a map name")
        if entry not in known:
            _fail(
                f"{what}[{index}]",
                f"this release publishes no archive for '{entry}'; it published "
                f"{sorted(known)}",
            )
    return list(names)


def rotation_arguments(rotation: list[str]) -> list[str]:
    """The launch arguments that play a rotation and keep it cycling.

    One `d<N>` cvar per entry, each loading its map and pointing `nextmap` at
    the next, and `vstr d1` to enter the cycle. They are `+set` lines and one
    command, so they are prepended to a profile's committed arguments:
    Com_StartupVariable applies every `set` line before the command buffer runs
    at all (ioq3 code/qcommon/common.c Com_Init), and `vstr d1` therefore
    precedes the profile's `+addbot` lines in buffer order — which it must,
    because addbot is forwarded to a game module that has to be running.
    """
    total = len(rotation)
    arguments: list[str] = []
    for index, name in enumerate(rotation, start=1):
        arguments += [
            "+set",
            ROTATION_CVAR.format(index=index),
            ROTATION_STEP.format(
                map=name, next=ROTATION_CVAR.format(index=index % total + 1)
            ),
        ]
    return arguments + ["+vstr", ROTATION_START]


def offline_map_arguments(rotation: list[str]) -> list[str]:
    """The launch argument the browser's offline slice starts its map with.

    Deliberately *not* `rotation_arguments`. The offline slice is a local listen
    server for one player and its bots; the product's multiplayer client starts
    no map at all and takes its rotation from the server it connects to. Putting
    the cycle here as well would make the browser's command line the binding
    constraint on how long a rotation may be — the loader adds its own
    render-size arguments — and that is the wrong direction: the client's set
    must be able to cover the server's rotation, never the reverse.

    The first entry is the map, because that is where a rotation starts and
    both halves have to read the one list the same way.
    """
    return ["+map", rotation[0]]


def check_no_committed_map(arguments: list[str], what: str) -> None:
    """Refuse a committed argument list that already chooses a map.

    This is the committed-side half of the rule the loader enforces at run time.
    The loader refuses to fetch without `?maps=` because both plausible defaults
    are wrong; the same reasoning applies one step earlier, to the arrays this
    repository publishes for a caller to pass verbatim. A stray rotation cvar
    left in one of them would be a default that no caller can see and that
    nothing downstream reports — the server would simply play a map its clients
    were never told to fetch.

    **Only the cvar half of this can fire on a committed profile, and saying so
    is the point.** Both callers compare the committed array against
    `expected_*_arguments()` before calling this, and that derivation emits only
    `+set` and `+addbot` — so a literal map command in a committed array is
    already refused one line earlier, by a different message. What *is* live is
    the `+set d<N>` branch: `CVAR_NAME` admits a cvar named `d1`, so a profile
    can legitimately declare one and the derivation will emit it. The command
    branch is a forward guard for a derivation that could one day emit more
    than it does, kept and labelled rather than believed in — the mistake this
    repository has already paid for is a check whose reach is assumed rather
    than stated.

    A caller that supplies no rotation at all is caught elsewhere and loudly: a
    mapless dedicated server never answers a `getstatus` the readiness contract
    accepts, so the omission fails the gate the integration already runs rather
    than surfacing at a map change.
    """
    rotation_cvar = re.compile(
        r"\A" + re.escape(ROTATION_CVAR.format(index="")) + r"[0-9]+\Z"
    )
    # Every command the pinned server registers that loads a map, plus `vstr`,
    # which can carry one indirectly (ioq3 code/server/sv_ccmds.c SV_AddOperatorCommands).
    for index, argument in enumerate(arguments):
        if argument in ("+map", "+devmap", "+spmap", "+spdevmap", "+vstr"):
            _fail(what, f"carries '{argument}': the map is a launch argument")
        if (
            argument == "+set"
            and index + 1 < len(arguments)
            and rotation_cvar.fullmatch(arguments[index + 1])
        ):
            _fail(
                what,
                f"sets the rotation cvar '{arguments[index + 1]}': the rotation is "
                "a launch argument and may not be committed",
            )


def max_rotation_length(
    fixed: list[str], limits: dict[str, int], names: list[str]
) -> int:
    """How many of `names`, in order, a rotation can hold beside `fixed`.

    Reported rather than assumed, and narrower than it looks. The cost of an
    entry is not a constant — the step names the map once and its own `d<N>`
    cvar twice, and `d10` is a byte longer than `d9` — so a bound stated as a
    map count is only true of a particular list.

    **This answers for prefixes of `names`, in the order given.** A rotation may
    repeat a map (`validate_rotation`), and fifteen entries of one long name
    cost more than the fifteen longest distinct ones, so a rotation this
    function's answer appears to permit can still be refused. The binding check
    is `check_command_line_budget` on the rotation that is actually launched.
    """
    for count in range(len(names), 0, -1):
        try:
            check_command_line_budget(
                rotation_arguments(names[:count]) + fixed, limits, "rotation"
            )
        except ArenaRuntimeError:
            continue
        return count
    return 0

def match_end_limits(engine_root: Path) -> set[str]:
    """The limit cvars the pinned `CheckExitRules` actually ends a level on.

    Read rather than remembered. The rule below is a claim about the gamecode,
    and a claim about a pin that nothing re-derives is the term of a gate that
    goes quietly wrong — the shape WP-D's review found in the systeminfo
    allowance. A gamecode that grew a fourth exit rule would otherwise leave
    this checking three out of four with nothing red.
    """
    source = engine_root / MATCH_END_SOURCE
    try:
        text = source.read_text(encoding="latin-1")
    except OSError as error:
        _fail("match end", f"cannot read {MATCH_END_SOURCE}: {error}")
    start = text.find(MATCH_END_FUNCTION)
    if start < 0:
        _fail(
            "match end",
            f"{MATCH_END_SOURCE} no longer defines CheckExitRules, so the rules "
            "that end a level cannot be read out of the pinned gamecode",
        )
    end = text.find("\n}", start)
    if end < 0:
        _fail("match end", f"{MATCH_END_SOURCE}: CheckExitRules has no end")
    found = set(MATCH_END_LIMIT.findall(text[start:end]))
    if not found:
        _fail(
            "match end",
            "the pinned CheckExitRules reads no limit cvar at all, so this rule "
            "would be satisfied by measuring nothing",
        )
    return found


def check_match_end_cvars(repo_root: Path, cvars: dict[str, Any], what: str) -> None:
    """A rotation can only advance if a match can end.

    This replaces a check that the rotation made unaskable. Until now both
    profiles required `fraglimit` to equal the *arena definition's* frag limit
    for the one committed map. Across a published set that is not a single
    value — the fragments declare 10, 15, 20 and 30 — so the equality
    is not merely inconvenient under a rotation, it is unsatisfiable.

    It was also never worth what it looked like. `arena.fraglimit` is written
    into the generated arena data, and **nothing reads that data outside
    `GT_SINGLE_PLAYER`**: every consumer in `G_LoadArenas` sits inside
    `if (g_gametype.integer == GT_SINGLE_PLAYER)` (ioq3 code/game/g_bot.c), and
    this profile is `GT_FFA`. The packaged q3_ui reads it only in the skirmish
    menus, which this product never enters — it launches straight into a game.
    So the equality bound a live cvar to a value the running game cannot see.
    That sentence is here so the binding is not reinstated later by someone who
    finds a cvar and an arena field with the same name; it has been written into
    this repository once already.

    What replaces it is a rule with live subject matter, and one the rotation
    itself creates. `CheckExitRules` ends a level on the frag limit or the time
    limit and on nothing else (ioq3 code/game/g_main.c), and `ExitLevel` is the
    only thing that runs `vstr nextmap`. A profile that zeroes both therefore
    commits a rotation that can never reach its second map — with no error
    anywhere, just a server that stays on one map forever.
    """
    limits = match_end_limits(repo_root / ENGINE_ROOT)
    uncovered = sorted(limits - set(MATCH_END_CVARS) - set(MATCH_END_UNREACHABLE))
    if uncovered:
        _fail(
            what,
            f"the pinned gamecode ends a level on {uncovered} as well, and this "
            "rule neither covers nor exempts them",
        )
    missing = sorted(set(MATCH_END_CVARS) - limits)
    if missing:
        _fail(
            what,
            f"the pinned gamecode's CheckExitRules no longer reads {missing}, so "
            "this rule is about a level exit that no longer exists",
        )
    values = []
    for name in MATCH_END_CVARS:
        value = cvars.get(name)
        if not isinstance(value, str) or not DECIMAL.fullmatch(value):
            _fail(f"{what}.{name}", "must be a non-negative decimal integer")
        values.append(int(value))
    if not any(values):
        _fail(
            what,
            "sets both "
            + " and ".join(MATCH_END_CVARS)
            + " to 0, so CheckExitRules never ends a level (ioq3 "
            "code/game/g_main.c) and a rotation could never advance past its "
            "first map",
        )


def expected_engine_arguments(profile: dict[str, Any]) -> list[str]:
    """The one derivation of the committed half of the engine command line.

    ``+set`` lines are read by ioq3's Com_StartupVariable and the remaining
    lines are executed from the command buffer in order
    (code/qcommon/common.c), so the map has to precede every ``+addbot`` and
    the bots follow G_SpawnBots' delay cadence.

    **The map is not in here.** It is a launch argument, supplied by whoever
    knows the rotation, and `offline_map_arguments` is prepended to this list
    rather than spliced into it — which is the same buffer order, because every
    ``+set`` line is applied before the command buffer runs at all.
    """
    arguments: list[str] = []
    for name in sorted(profile["cvars"]):
        arguments += ["+set", name, profile["cvars"][name]]
    for index, bot in enumerate(profile["bots"]):
        delay = BOT_BEGIN_DELAY_BASE_MS + index * BOT_BEGIN_DELAY_INCREMENT_MS
        arguments += ["+addbot", bot["name"], str(bot["skill"]), "free", str(delay)]
    return arguments


def _validate_bots(profile: dict[str, Any]) -> None:
    bots = _array(profile.get("bots"), "profile.bots")
    if not bots:
        _fail("profile.bots", "must name at least one bot")
    seen: set[str] = set()
    for entry in bots:
        bot = _object(entry, "profile.bots entry")
        _exact_keys(bot, ("name", "skill"), "profile.bots entry")
        name = _string(bot.get("name"), "profile.bots[].name")
        if not BOT_NAME.fullmatch(name):
            _fail("profile.bots[].name", f"'{name}' is not a plain bot name")
        if name in seen:
            _fail("profile.bots", f"names '{name}' twice")
        seen.add(name)
        skill = bot.get("skill")
        low, high = BOT_SKILL_RANGE
        if (
            not isinstance(skill, int)
            or isinstance(skill, bool)
            or not low <= skill <= high
        ):
            _fail("profile.bots[].skill", f"must be an integer in {low}..{high}")


def _validate_cvars(profile: dict[str, Any], repo_root: Path) -> None:
    cvars = _object(profile.get("cvars"), "profile.cvars")
    if not cvars:
        _fail("profile.cvars", "is empty")
    for name, value in cvars.items():
        if not CVAR_NAME.fullmatch(name):
            _fail("profile.cvars", f"'{name}' is not a cvar name")
        if (
            not isinstance(value, str)
            or value == ""
            or any(character.isspace() for character in value)
        ):
            _fail(
                f"profile.cvars.{name}", "must be a non-empty value without whitespace"
            )
    for name in RUNTIME_DERIVED_CVARS:
        if name in cvars:
            _fail(
                "profile.cvars",
                f"'{name}' is derived from the live canvas and must not be committed",
            )
    _exact_keys(
        _object(profile.get("cvarNotes"), "profile.cvarNotes"),
        tuple(cvars),
        "profile.cvarNotes",
    )
    for name in cvars:
        _string(profile["cvarNotes"][name], f"profile.cvarNotes.{name}")
    if cvars.get("com_basegame") != profile["basegame"]:
        _fail("profile.cvars.com_basegame", "must equal profile.basegame")
    if cvars.get("g_gametype") != FFA_GAMETYPE:
        _fail(
            "profile.cvars.g_gametype",
            f"must be '{FFA_GAMETYPE}' (GT_FFA) for this profile",
        )
    if cvars.get("net_enabled") != "0":
        _fail("profile.cvars.net_enabled", "must be '0': the vertical slice is offline")
    if cvars.get("r_allowResize") != "1":
        _fail("profile.cvars.r_allowResize", "must be '1': runtime resize is supported")
    if cvars.get("r_fullscreen") != "0":
        _fail(
            "profile.cvars.r_fullscreen",
            "must be '0': the HTML stage, not SDL, owns browser fullscreen",
        )
    if cvars.get("model") != profile["playerModel"]:
        _fail("profile.cvars.model", "must equal profile.playerModel")
    if cvars.get("headmodel") != profile["playerModel"]:
        _fail("profile.cvars.headmodel", "must equal profile.playerModel")
    check_match_end_cvars(repo_root, cvars, "profile.cvars")


def _validate_markers(profile: dict[str, Any]) -> None:
    markers = _object(profile.get("readyMarkers"), "profile.readyMarkers")
    _exact_keys(markers, READY_MARKER_NAMES, "profile.readyMarkers")
    for name in READY_MARKER_NAMES:
        _string(markers[name], f"profile.readyMarkers.{name}")
    # The map is a launch argument, so the marker that names it is a template
    # and the loader fills it in with the map it actually started. Keeping the
    # name in the marker is the point: a bare `Server: ` prefix would be
    # satisfied by *any* spawn, and what this marker is evidence for is that
    # the map the rotation asked for is the map that came up.
    if markers["serverSpawned"] != SERVER_SPAWNED_MARKER:
        _fail(
            "profile.readyMarkers.serverSpawned",
            f"must be '{SERVER_SPAWNED_MARKER}' — the exact Com_Printf of ioq3 "
            f"code/server/sv_init.c with the started map as "
            f"'{MARKER_MAP_PLACEHOLDER}'",
        )
    _exact_keys(
        _object(profile.get("readyMarkerNotes"), "profile.readyMarkerNotes"),
        READY_MARKER_NAMES,
        "profile.readyMarkerNotes",
    )
    for name in READY_MARKER_NAMES:
        _string(profile["readyMarkerNotes"][name], f"profile.readyMarkerNotes.{name}")


def expected_served_path(
    manifest_name: str, artifact_path: str, entry: dict[str, Any]
) -> str:
    """The served path an artifact must have, digest and all.

    Two things are decoupled here on purpose. The **manifest path** is a stable
    literal, so the records that name an archive do not move when its bytes
    change; the **served path** carries the artifact's own digest, so a
    published URL is immutable and a returning player re-downloads nothing.

    Deriving the digest half rather than trusting it is the point: a name
    published with a stale hash over current bytes would be cached `immutable`
    for a year, the loader would throw on the digest mismatch, and the client
    would have no recovery path.
    """
    prefix = MANIFEST_PREFIXES[manifest_name]
    if manifest_name not in HASHED_SERVED_MANIFESTS:
        return f"{prefix}/{artifact_path}"
    digest = _string(entry.get("sha256"), f"{manifest_name} manifest sha256")
    if not SHA256.fullmatch(digest):
        _fail(f"{manifest_name} manifest", f"'{artifact_path}' has no SHA-256")
    directory, _, name = artifact_path.rpartition("/")
    stem, dot, suffix = name.partition(".")
    short = digest[:SERVED_DIGEST_PREFIX_LENGTH]
    hashed = f"{stem}-{short}{dot}{suffix}"
    return f"{prefix}/{directory}/{hashed}" if directory else f"{prefix}/{hashed}"


def _validate_artifacts(
    profile: dict[str, Any], manifests: dict[str, dict[str, dict[str, Any]]]
) -> None:
    artifacts = _array(profile.get("artifacts"), "profile.artifacts")
    if not artifacts:
        _fail("profile.artifacts", "is empty")
    served_paths: set[str] = set()
    filesystem_paths: set[str] = set()
    role_counts = dict.fromkeys(ARTIFACT_ROLES, 0)
    for entry in artifacts:
        artifact = _object(entry, "profile.artifacts entry")
        role = _string(artifact.get("role"), "profile.artifacts[].role")
        if role not in ARTIFACT_ROLES:
            _fail("profile.artifacts[].role", f"'{role}' is not a known role")
        expected_keys = ("manifest", "path", "role", "served")
        if role == "filesystem":
            expected_keys += ("fsPath",)
        _exact_keys(
            artifact, expected_keys, f"profile.artifacts[{artifact.get('served')}]"
        )
        role_counts[role] += 1

        manifest_name = _string(
            artifact.get("manifest"), "profile.artifacts[].manifest"
        )
        if manifest_name not in manifests:
            _fail(
                "profile.artifacts[].manifest",
                f"'{manifest_name}' is not a declared manifest",
            )
        artifact_path = _string(artifact.get("path"), "profile.artifacts[].path")
        if not GAME_PATH.fullmatch(artifact_path):
            _fail(
                "profile.artifacts[].path",
                f"'{artifact_path}' is not a plain relative path",
            )
        if artifact_path not in manifests[manifest_name]:
            _fail(
                f"profile.artifacts[{artifact_path}]",
                f"the {manifest_name} manifest does not declare it",
            )
        if artifact_path in FORBIDDEN_ARTIFACT_PATHS or artifact_path.startswith(
            FORBIDDEN_ARTIFACT_PREFIXES
        ):
            _fail(
                f"profile.artifacts[{artifact_path}]",
                "is build evidence or off-profile output and may not be served",
            )
        for suffix in FORBIDDEN_SUFFIXES_BY_MANIFEST[manifest_name]:
            if artifact_path.endswith(suffix):
                _fail(
                    f"profile.artifacts[{artifact_path}]",
                    f"a '{manifest_name}' artifact may not end in '{suffix}'",
                )

        served = _string(artifact.get("served"), "profile.artifacts[].served")
        expected_served = expected_served_path(
            manifest_name, artifact_path, manifests[manifest_name][artifact_path]
        )
        if served != expected_served:
            _fail(
                f"profile.artifacts[{artifact_path}].served",
                f"must be '{expected_served}'",
            )
        if served in served_paths:
            _fail("profile.artifacts", f"serves '{served}' twice")
        served_paths.add(served)

        if role == "filesystem":
            fs_path = _string(artifact.get("fsPath"), "profile.artifacts[].fsPath")
            expected_fs = f"/{profile['basegame']}/"
            if not fs_path.startswith(expected_fs):
                _fail(
                    f"profile.artifacts[{artifact_path}].fsPath",
                    f"must start with '{expected_fs}'",
                )
            if not GAME_PATH.fullmatch(fs_path.lstrip("/")):
                _fail(
                    f"profile.artifacts[{artifact_path}].fsPath",
                    "is not a plain absolute path",
                )
            if fs_path in filesystem_paths:
                _fail("profile.artifacts", f"writes '{fs_path}' twice")
            filesystem_paths.add(fs_path)
            if manifest_name in HASHED_SERVED_MANIFESTS:
                # The name the engine sees is this basename, and PK3 load order
                # is by that name: `FS_AddGameDirectory` sorts ascending and
                # prepends, so the lowest-named archive wins a shader
                # definition while the highest wins a file. The content build
                # checks cross-archive shader precedence against the *manifest*
                # names, so the two must be the same name or that check would
                # model an order the engine does not use.
                expected_name = artifact_path.rsplit("/", 1)[-1]
                if fs_path.rsplit("/", 1)[-1] != expected_name:
                    _fail(
                        f"profile.artifacts[{artifact_path}].fsPath",
                        f"must end in '{expected_name}': the engine's PK3 load "
                        "order is by this name",
                    )

    for role in SINGLETON_ROLES:
        if role_counts[role] != 1:
            _fail("profile.artifacts", f"must declare exactly one '{role}' artifact")
    if role_counts["filesystem"] == 0:
        _fail("profile.artifacts", "must declare at least one 'filesystem' artifact")


def _validate_config_files(profile: dict[str, Any], repo_root: Path) -> None:
    """The product-owned engine configuration the game directory must carry."""
    entries = _array(profile.get("configFiles"), "profile.configFiles")
    if not entries:
        _fail("profile.configFiles", "is empty")
    served_paths: set[str] = set()
    filesystem_paths: set[str] = set()
    for entry in entries:
        record = _object(entry, "profile.configFiles entry")
        _exact_keys(record, ("fsPath", "served", "source"), "profile.configFiles entry")
        source = _string(record.get("source"), "profile.configFiles[].source")
        if "/" in source or source.startswith("."):
            _fail(
                "profile.configFiles[].source", f"'{source}' is not a plain file name"
            )
        if not (repo_root / LOADER_SOURCE_DIRECTORY / source).is_file():
            _fail(
                "profile.configFiles[].source",
                f"{LOADER_SOURCE_DIRECTORY}/{source} does not exist",
            )
        served = _string(record.get("served"), "profile.configFiles[].served")
        if served != source:
            _fail(
                "profile.configFiles[].served", f"must equal the source name '{source}'"
            )
        if served in served_paths or served in LOADER_FILES or served == PROFILE_SERVED:
            _fail("profile.configFiles", f"serves '{served}' twice")
        served_paths.add(served)
        fs_path = _string(record.get("fsPath"), "profile.configFiles[].fsPath")
        expected = f"/{profile['basegame']}/{source}"
        if fs_path != expected:
            _fail("profile.configFiles[].fsPath", f"must be '{expected}'")
        filesystem_paths.add(fs_path)

    required = f"/{profile['basegame']}/{REQUIRED_CONFIG_FILE}"
    if required not in filesystem_paths:
        _fail(
            "profile.configFiles",
            f"must place '{REQUIRED_CONFIG_FILE}' at {required}; the engine refuses to "
            "start without it (ioq3 code/qcommon/files.c FS_InitFilesystem)",
        )


def manifest_map_inputs(manifest: dict[str, Any], what: str) -> dict[str, str]:
    """The content manifest's per-map fragment inputs, as map name -> identity.

    The root recipe deliberately carries no list of maps — it is the base
    archive's own selection input, and a map set in it would move the base's
    bytes whenever a map was added. The fragments enter the release identity
    here instead, one manifest input each, and the manifest is an authority
    whose digest is a `compatibility` member.
    """
    inputs = _array(manifest.get("inputs"), f"{what}.inputs")
    found: dict[str, str] = {}
    for entry in inputs:
        record = _object(entry, f"{what}.inputs entry")
        identifier = _string(record.get("id"), f"{what}.inputs[].id")
        if not identifier.startswith(MAP_FRAGMENT_INPUT_PREFIX):
            continue
        name = identifier[len(MAP_FRAGMENT_INPUT_PREFIX) :]
        if not MAP_NAME.fullmatch(name):
            _fail(f"{what}.inputs", f"'{identifier}' is not a map fragment input")
        found[name] = _string(record.get("identity"), f"{what}.inputs[].identity")
    return found


def load_map_fragment(
    repo_root: Path, map_name: str, manifest_relative: str
) -> dict[str, Any]:
    """One committed per-map recipe fragment, bound to the content manifest.

    Reading the file alone would be fail-open: the fragment decides what its
    map archive holds, so it is read only after its digest has been checked
    against the identity the content manifest records for it.
    """
    manifest = _object(
        _load_json(repo_root / manifest_relative, manifest_relative), manifest_relative
    )
    identities = manifest_map_inputs(manifest, manifest_relative)
    if map_name not in identities:
        _fail(
            f"{manifest_relative}.inputs",
            f"records no fragment for map '{map_name}'",
        )
    relative = f"{MAP_FRAGMENT_DIRECTORY}/{map_name}.json"
    source = repo_root / relative
    if not source.is_file():
        _fail(relative, "does not exist")
    digest = f"sha256:{file_sha256(source)}"
    if digest != identities[map_name]:
        _fail(
            relative,
            f"is {digest}, but the content manifest records "
            f"{identities[map_name]}",
        )
    fragment = _object(_load_json(source, relative), relative)
    arena = _object(fragment.get("arena"), f"{relative}.arena")
    if arena.get("map") != map_name:
        _fail(f"{relative}.arena", f"must define map '{map_name}'")
    types = _string(arena.get("type"), f"{relative}.arena.type").split()
    unknown = sorted(set(types) - set(SUPPORTED_ARENA_TYPES))
    if unknown or not types:
        _fail(
            f"{relative}.arena.type",
            f"must be a non-empty set drawn from {list(SUPPORTED_ARENA_TYPES)}; "
            f"the OpenArena-only tags are normalised away rather than copied "
            f"(unsupported: {unknown})",
        )
    if types != [PUBLISHED_ARENA_TYPE]:
        _fail(
            f"{relative}.arena.type",
            f"must be exactly '{PUBLISHED_ARENA_TYPE}': the map a server plays "
            "is a launch argument, so a rotation may reach any published "
            "archive and both profiles commit GT_FFA, and the normalisation "
            "drops the rest of the upstream type rather than carrying it for "
            "some maps and not others",
        )
    if not DECIMAL.fullmatch(_string(arena.get("fraglimit"), f"{relative}.arena.fraglimit")):
        _fail(f"{relative}.arena.fraglimit", "must be a non-negative decimal integer")
    return fragment


def published_maps(repo_root: Path, manifest_relative: str) -> list[str]:
    """Every map this release publishes, each fragment read and checked.

    Reading them all is the point. The profile used to name one map and check
    that map's arena; under a rotation the archive a server starts is chosen
    after the release is built, so the property has to hold for every archive
    the release publishes or it holds for nothing.
    """
    manifest = _object(
        _load_json(repo_root / manifest_relative, manifest_relative), manifest_relative
    )
    names = sorted(manifest_map_inputs(manifest, manifest_relative))
    if not names:
        _fail(f"{manifest_relative}.inputs", "records no map fragment at all")
    for name in names:
        load_map_fragment(repo_root, name, manifest_relative)
    return names


def _validate_against_recipe(
    profile: dict[str, Any],
    recipe: dict[str, Any],
    repo_root: Path,
    manifests: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Bind the loader profile to the audited content pack it starts."""
    recipe_profile = _object(recipe.get("profile"), "recipe.profile")
    package = _object(recipe.get("package"), "recipe.package")

    if profile["package"] != package.get("id"):
        _fail(
            "profile.package", f"must equal the recipe package id '{package.get('id')}'"
        )
    manifest_relative = _string(
        _object(profile.get("manifests"), "profile.manifests").get("content"),
        "profile.manifests.content",
    )
    # Every published map, not one committed map: `published_maps` reads each
    # fragment through its manifest-recorded digest and requires each to declare
    # an arena this profile's gametype can start.
    published_maps(repo_root, manifest_relative)
    if profile["playerModel"] not in _array(
        recipe_profile.get("playerModels"), "recipe.profile.playerModels"
    ):
        _fail(
            "profile.playerModel",
            "is not a player presentation the content pack packages",
        )
    recipe_bots = {
        _string(
            _object(bot, "recipe.profile.bots entry").get("name"),
            "recipe.profile.bots[].name",
        )
        for bot in _array(recipe_profile.get("bots"), "recipe.profile.bots")
    }
    for bot in profile["bots"]:
        if bot["name"] not in sorted(recipe_bots):
            _fail(
                "profile.bots",
                f"'{bot['name']}' is not a bot the content pack packages",
            )

    # The profile declares *every* published archive, not the subset a given
    # rotation needs: WP11's integrity property is that the served set is
    # committed and digest-bound, and a runtime selection from a committed set
    # is no new trust decision. The set is the base plus one archive per map
    # fragment the content manifest records.
    template = _string(recipe.get("mapPackTemplate"), "recipe.mapPackTemplate")
    manifest = _object(
        _load_json(repo_root / manifest_relative, manifest_relative), manifest_relative
    )
    base_pack = _string(recipe.get("basePackPath"), "recipe.basePackPath")
    fragment_maps = manifest_map_inputs(manifest, manifest_relative)
    expected_packs = sorted(
        [base_pack] + [template.format(map=name) for name in fragment_maps]
    )
    content_paths = sorted(
        artifact["path"]
        for artifact in profile["artifacts"]
        if artifact["manifest"] == "content"
    )
    if content_paths != expected_packs:
        _fail(
            "profile.artifacts",
            f"must serve exactly the recipe's archives {expected_packs}",
        )
    _validate_content_records(
        manifests, manifest_relative, base_pack, template, fragment_maps, content_paths
    )


def _validate_content_records(
    manifests: dict[str, dict[str, dict[str, Any]]],
    manifest_relative: str,
    base_pack: str,
    template: str,
    fragment_maps: dict[str, str],
    published: list[str],
) -> None:
    """The per-archive selection key and cost figures the content manifest adds.

    A caller choosing a rotation needs two things out of the release: which
    archive is which map, and what one costs it. Both live here rather than in
    the profile so that there is one home per fact — the manifest is generated
    from the archives it describes, is an authority, and is already fetched
    before any archive is.

    Fail-closed in both directions: a map archive without its records, a base
    that claims a map, an engine artifact borrowing the vocabulary, or a map
    name that no committed fragment declares are all failures.
    """
    for path, entry in sorted(manifests["engine"].items()):
        present = sorted(name for name in CONTENT_ARTIFACT_RECORDS if name in entry)
        if present:
            _fail(
                f"{PROFILE_SOURCE}: engine manifest artifact '{path}'",
                f"carries the content-only records {present}",
            )
    for path, entry in sorted(manifests["content"].items()):
        if path in published:
            continue
        # An archive the release does not publish may not look like one that
        # does: a stray manifest entry claiming a map would offer a consumer a
        # selection key for something the profile never serves.
        if "map" in entry:
            _fail(
                f"{manifest_relative}.artifacts[{path}].map",
                "is not one of the archives this release publishes",
            )
    for path in sorted(published):
        entry = manifests["content"][path]
        what = f"{manifest_relative}.artifacts[{path}]"
        uncompressed = entry.get("uncompressedSize")
        if (
            not isinstance(uncompressed, int)
            or isinstance(uncompressed, bool)
            or uncompressed <= 0
        ):
            _fail(f"{what}.uncompressedSize", "must be a positive integer")
        if path == base_pack:
            for name in ("map", "peakHunkBytes"):
                if name in entry:
                    _fail(
                        f"{what}.{name}",
                        "the base archive carries no map and no map's peak hunk",
                    )
            continue
        name = entry.get("map")
        if not isinstance(name, str) or name not in fragment_maps:
            _fail(
                f"{what}.map",
                f"must name one of the committed map fragments "
                f"{sorted(fragment_maps)}",
            )
        if template.format(map=name) != path:
            _fail(f"{what}.map", f"'{name}' does not match this archive's path")
        peak = entry.get("peakHunkBytes")
        if not isinstance(peak, int) or isinstance(peak, bool) or peak <= 0:
            _fail(f"{what}.peakHunkBytes", "must be a positive integer")
    declared = {
        manifests["content"][path]["map"] for path in published if path != base_pack
    }
    if declared != set(fragment_maps):
        _fail(
            f"{manifest_relative}.artifacts",
            f"declares maps {sorted(declared)}, and the committed fragments are "
            f"{sorted(fragment_maps)}",
        )


def load_relay_profile(repo_root: Path) -> dict[str, Any]:
    """Validate the non-secret, fixed half of the WP7 browser profile."""
    profile = _object(
        _load_json(repo_root / RELAY_PROFILE_SOURCE, RELAY_PROFILE_SOURCE),
        "relay profile",
    )
    _exact_keys(profile, RELAY_PROFILE_KEYS, "relay profile")
    expected_scalars = {
        "formatVersion": 1,
        "mode": "relay-client",
        "connectFamily": "-6",
        "innerDatagramFloor": 768,
        "fragmentSize": 704,
        "receiveQueueDepth": 256,
        "singleDatagramOverhead": 42,
        "keepAliveIntervalSource": "runtime",
    }
    for name, expected in expected_scalars.items():
        if profile.get(name) != expected:
            _fail(
                f"relay profile.{name}",
                f"must equal the decided WP7 value {expected!r}",
            )
    cvars = _object(profile.get("cvars"), "relay profile.cvars")
    _exact_keys(cvars, tuple(RELAY_PROFILE_CVARS), "relay profile.cvars")
    if cvars != RELAY_PROFILE_CVARS:
        _fail("relay profile.cvars", "does not match the decided WP7 client profile")
    return profile


def load_profile(repo_root: Path) -> dict[str, Any]:
    """Read, validate and cross-check the committed content configuration."""
    profile = _object(_load_json(repo_root / PROFILE_SOURCE, PROFILE_SOURCE), "profile")
    _exact_keys(profile, PROFILE_KEYS, "profile")
    if profile.get("formatVersion") != 1:
        _fail("profile.formatVersion", "must be 1")
    _array(profile.get("$comment"), "profile.$comment")
    for key in ("basegame", "package", "playerModel"):
        _string(profile.get(key), f"profile.{key}")
    if not BASEGAME_NAME.fullmatch(profile["basegame"]):
        _fail(
            "profile.basegame", f"'{profile['basegame']}' is not a game directory name"
        )
    if profile["basegame"] == IOQ3_RETAIL_BASEGAME:
        _fail(
            "profile.basegame",
            f"may not be '{IOQ3_RETAIL_BASEGAME}': the engine then demands the retail "
            "pak0..pak8 and refuses to start (ioq3 code/qcommon/files.c FS_CheckPak0)",
        )

    _validate_bots(profile)
    _validate_cvars(profile, repo_root)
    _validate_markers(profile)
    _validate_config_files(profile, repo_root)

    declared = _object(profile.get("manifests"), "profile.manifests")
    _exact_keys(declared, MANIFEST_NAMES, "profile.manifests")
    manifests: dict[str, dict[str, dict[str, Any]]] = {}
    for name in MANIFEST_NAMES:
        relative = _string(declared[name], f"profile.manifests.{name}")
        if relative.startswith("/") or ".." in relative.split("/"):
            _fail(f"profile.manifests.{name}", "must be a repository-relative path")
        manifests[name] = manifest_index(
            _load_json(repo_root / relative, relative), relative
        )

    _validate_artifacts(profile, manifests)

    arguments = _array(profile.get("engineArguments"), "profile.engineArguments")
    expected = expected_engine_arguments(profile)
    if arguments != expected:
        _fail(
            "profile.engineArguments",
            "is not the derivation of the profile's own fields; expected "
            + " ".join(expected),
        )
    # The committed half may not carry a map, or the rotation would have a
    # default hiding inside the list a caller passes verbatim — which is the
    # one thing the loader refuses to have on its own side.
    check_no_committed_map(arguments, "profile.engineArguments")
    limits = check_command_line_limits(
        repo_root, profile.get("engineCommandLine"), "profile.engineCommandLine"
    )
    notes = _object(
        profile.get("engineCommandLineNotes"), "profile.engineCommandLineNotes"
    )
    _exact_keys(notes, COMMAND_LINE_LIMIT_KEYS, "profile.engineCommandLineNotes")
    for name in COMMAND_LINE_LIMIT_KEYS:
        if len(_string(notes[name], f"profile.engineCommandLineNotes.{name}")) < 20:
            _fail(f"profile.engineCommandLineNotes.{name}", "must state the engine site")
    profile["_commandLineLimits"] = limits

    _validate_against_recipe(
        profile,
        _object(_load_json(repo_root / "content/pack-recipe.json", "recipe"), "recipe"),
        repo_root,
        manifests,
    )
    check_systeminfo_budget(repo_root, profile)
    load_relay_profile(repo_root)
    profile["_manifests"] = manifests
    return profile


def served_files(repo_root: Path, profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The complete served set: served path -> source and expected identity."""
    files: dict[str, dict[str, Any]] = {}
    for served, source in RUNTIME_SOURCE_FILES.items():
        files[served] = {
            "kind": "loader",
            "source": repo_root / source,
        }
    files[PROFILE_SERVED] = {"kind": "profile", "source": repo_root / PROFILE_SOURCE}
    for entry in profile["configFiles"]:
        files[entry["served"]] = {
            "kind": "config",
            "source": repo_root / LOADER_SOURCE_DIRECTORY / entry["source"],
        }
    for name in MANIFEST_NAMES:
        relative = profile["manifests"][name]
        files[relative] = {"kind": "manifest", "source": repo_root / relative}
    for artifact in profile["artifacts"]:
        entry = profile["_manifests"][artifact["manifest"]][artifact["path"]]
        files[artifact["served"]] = {
            "kind": "artifact",
            "manifest": artifact["manifest"],
            "artifactPath": artifact["path"],
            "sha256": entry["sha256"],
            "size": entry["size"],
            "hashedName": artifact["manifest"] in HASHED_SERVED_MANIFESTS,
        }
    return files


def _artifact_source(directories: dict[str, Path], artifact: dict[str, Any]) -> Path:
    return directories[artifact["manifest"]] / artifact["path"]


def stage(
    repo_root: Path,
    target: Path,
    *,
    engine_dir: Path,
    content_dir: Path,
) -> dict[str, Any]:
    """Assemble the served tree, refusing any artifact that is not the committed one."""
    profile = load_profile(repo_root)
    directories = {"engine": engine_dir, "content": content_dir}
    for name, directory in directories.items():
        if not directory.is_dir():
            _fail(f"{name} directory", f"{directory} does not exist; build it first")

    files = served_files(repo_root, profile)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    verified: list[dict[str, Any]] = []
    for served, entry in sorted(files.items()):
        destination = target / served
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry["kind"] == "artifact":
            artifact = next(
                item for item in profile["artifacts"] if item["served"] == served
            )
            source = _artifact_source(directories, artifact)
            if not source.is_file():
                _fail(served, f"{source} does not exist; build it first")
            size = source.stat().st_size
            digest = file_sha256(source)
            if digest != entry["sha256"] or size != entry["size"]:
                _fail(
                    served,
                    f"{source} is not the committed {artifact['manifest']} artifact "
                    f"(expected sha256:{entry['sha256']} {entry['size']} bytes, "
                    f"got sha256:{digest} {size} bytes)",
                )
            verified.append(
                {
                    "served": served,
                    "sha256": digest,
                    "size": size,
                    "source": str(source),
                }
            )
        else:
            source = entry["source"]
            if not source.is_file():
                _fail(served, f"{source} does not exist")
        shutil.copyfile(source, destination)

    report = {
        "package": profile["package"],
        "servedFiles": sorted(files),
        "artifacts": verified,
        "totalArtifactBytes": sum(item["size"] for item in verified),
        "engineArguments": profile["engineArguments"],
    }
    verify_staged(repo_root, target)
    return report


def verify_staged(repo_root: Path, target: Path) -> dict[str, Any]:
    """Re-read a staged tree and require exactly the expected files and digests."""
    profile = load_profile(repo_root)
    expected = served_files(repo_root, profile)

    if not target.is_dir():
        _fail("staged tree", f"{target} does not exist")
    present = set()
    for path in sorted(target.rglob("*")):
        if path.is_dir():
            if path.is_symlink():
                _fail(
                    "staged tree",
                    f"{path.relative_to(target)} is a symlinked directory",
                )
            continue
        if path.is_symlink() or not path.is_file():
            _fail("staged tree", f"{path.relative_to(target)} is not a regular file")
        present.add(path.relative_to(target).as_posix())

    unknown = sorted(present - set(expected))
    if unknown:
        _fail("staged tree", f"contains files the profile does not declare: {unknown}")
    missing = sorted(set(expected) - present)
    if missing:
        _fail("staged tree", f"is missing declared files: {missing}")

    for served, entry in sorted(expected.items()):
        path = target / served
        if entry["kind"] == "artifact":
            digest = file_sha256(path)
            size = path.stat().st_size
            if digest != entry["sha256"] or size != entry["size"]:
                _fail(
                    f"staged {served}", "does not match the committed manifest identity"
                )
        else:
            if file_sha256(path) != file_sha256(entry["source"]):
                _fail(f"staged {served}", f"differs from {entry['source']}")
    release_index = repo_root / "release/browser-release.json"
    if release_index.is_file():
        from release_index import ReleaseIndexError, validate_release_index

        try:
            validate_release_index(repo_root, expected, staged_root=target)
        except ReleaseIndexError as error:
            _fail("release index", str(error))
    return {"servedFiles": sorted(expected)}
