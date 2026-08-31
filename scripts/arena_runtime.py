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
from typing import Any

CHUNK_SIZE = 1024 * 1024

PROFILE_SOURCE = "arena/game-profile.json"
PROFILE_SERVED = "game-profile.json"
LOADER_SOURCE_DIRECTORY = "arena"
RUNTIME_SOURCE_FILES = {
    "index.html": "arena/index.html",
    "loader.js": "arena/loader.js",
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
    "formatVersion",
    "manifests",
    "map",
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
MAP_NAME = re.compile(r"\A[a-z0-9][a-z0-9_]*\Z")
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
        index[artifact_path] = {"sha256": digest, "size": size}
    if not index:
        _fail(f"{what}.artifacts", "is empty")
    return index


def expected_engine_arguments(profile: dict[str, Any]) -> list[str]:
    """The one derivation of the engine command line from the profile.

    ``+set`` lines are read by ioq3's Com_StartupVariable and the remaining
    lines are executed from the command buffer in order
    (code/qcommon/common.c), so ``+map`` must precede every ``+addbot`` and the
    bots follow G_SpawnBots' delay cadence.
    """
    arguments: list[str] = []
    for name in sorted(profile["cvars"]):
        arguments += ["+set", name, profile["cvars"][name]]
    arguments += ["+map", profile["map"]]
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


def _validate_cvars(profile: dict[str, Any]) -> None:
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
    if cvars.get("model") != profile["playerModel"]:
        _fail("profile.cvars.model", "must equal profile.playerModel")
    if cvars.get("headmodel") != profile["playerModel"]:
        _fail("profile.cvars.headmodel", "must equal profile.playerModel")


def _validate_markers(profile: dict[str, Any]) -> None:
    markers = _object(profile.get("readyMarkers"), "profile.readyMarkers")
    _exact_keys(markers, READY_MARKER_NAMES, "profile.readyMarkers")
    for name in READY_MARKER_NAMES:
        _string(markers[name], f"profile.readyMarkers.{name}")
    expected = f"Server: {profile['map']}"
    if markers["serverSpawned"] != expected:
        _fail(
            "profile.readyMarkers.serverSpawned",
            f"must be '{expected}' (ioq3 code/server/sv_init.c)",
        )
    _exact_keys(
        _object(profile.get("readyMarkerNotes"), "profile.readyMarkerNotes"),
        READY_MARKER_NAMES,
        "profile.readyMarkerNotes",
    )
    for name in READY_MARKER_NAMES:
        _string(profile["readyMarkerNotes"][name], f"profile.readyMarkerNotes.{name}")


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
        expected_served = f"{MANIFEST_PREFIXES[manifest_name]}/{artifact_path}"
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


def _validate_against_recipe(profile: dict[str, Any], recipe: dict[str, Any]) -> None:
    """Bind the loader profile to the audited content pack it starts."""
    recipe_profile = _object(recipe.get("profile"), "recipe.profile")
    arena = _object(recipe_profile.get("arena"), "recipe.profile.arena")
    package = _object(recipe.get("package"), "recipe.package")

    if profile["package"] != package.get("id"):
        _fail(
            "profile.package", f"must equal the recipe package id '{package.get('id')}'"
        )
    if profile["map"] != recipe_profile.get("map") or profile["map"] != arena.get(
        "map"
    ):
        _fail("profile.map", "must equal the map the content recipe assembles")
    if arena.get("type") != "ffa":
        _fail(
            "recipe.profile.arena.type", "the loader profile only starts an FFA arena"
        )
    if profile["playerModel"] not in _array(
        recipe_profile.get("playerModels"), "recipe.profile.playerModels"
    ):
        _fail(
            "profile.playerModel",
            "is not a player presentation the content pack packages",
        )
    if profile["cvars"].get("fraglimit") != arena.get("fraglimit"):
        _fail("profile.cvars.fraglimit", "must equal the recipe arena's frag limit")

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

    pack_path = _string(recipe.get("packPath"), "recipe.packPath")
    content_paths = [
        artifact["path"]
        for artifact in profile["artifacts"]
        if artifact["manifest"] == "content"
    ]
    if content_paths != [pack_path]:
        _fail(
            "profile.artifacts", f"must serve exactly the recipe's pack '{pack_path}'"
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
    for key in ("basegame", "map", "package", "playerModel"):
        _string(profile.get(key), f"profile.{key}")
    if not MAP_NAME.fullmatch(profile["map"]):
        _fail("profile.map", f"'{profile['map']}' is not a map name")
    if not MAP_NAME.fullmatch(profile["basegame"]):
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
    _validate_cvars(profile)
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

    _validate_against_recipe(
        profile,
        _object(_load_json(repo_root / "content/pack-recipe.json", "recipe"), "recipe"),
    )
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
        "map": profile["map"],
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
    return {"servedFiles": sorted(expected)}
