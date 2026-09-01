# SPDX-License-Identifier: GPL-2.0-or-later
"""The native side of the arena: the dedicated server image and its test client.

This module owns WP5's packaging discipline, and it is the WP4 discipline
applied to a container instead of a web serve. It answers two questions
fail-closed:

* **which bytes may the server image contain** — exactly the engine binary this
  repository built from the pinned commit, the QVM the accepted WP1 build
  emitted, the audited WP3 content pack and the product's own configuration,
  each copied only after its SHA-256 and byte length match the committed
  manifest entry;
* **which command line may each side run** — the one derivation of
  ``native/server-profile.json``'s declarative fields, which the committed
  argument lists must equal exactly.

The profile is additionally bound to ``arena/game-profile.json`` and
``content/pack-recipe.json``, so the native server cannot quietly run a
different game directory, map, package or frag limit from the browser slice it
is supposed to match.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from arena_runtime import (
    ArenaRuntimeError,
    file_sha256,
    load_map_fragment,
    manifest_index,
    manifest_map_inputs,
)

PROFILE_SOURCE = "native/server-profile.json"
CONFIG_SOURCE_DIRECTORY = "native"
BROWSER_PROFILE_SOURCE = "arena/game-profile.json"
RECIPE_SOURCE = "content/pack-recipe.json"

ENGINE_MANIFEST = "manifests/browser-client.json"
CONTENT_MANIFEST = "provenance/arena-web-ffa-content-manifest.json"

PROFILE_KEYS = (
    "$comment",
    "basegame",
    "bots",
    "client",
    "clientArguments",
    "configFiles",
    "cvarNotes",
    "cvars",
    "formatVersion",
    "gameDirectory",
    "map",
    "package",
    "playerModel",
    "port",
    "runtimeBaseCopyrightFiles",
    "serverArguments",
    "serverBinary",
)

CONFIG_ROLES = ("client", "server")

# ioq3 code/qcommon/files.c: FS_CheckPak0 leaves com_standalone at 0 whenever
# com_basegame is ioquake3's own retail game directory, and the engine then
# refuses to start without pak0..pak8.
IOQ3_RETAIL_BASEGAME = "baseq3"

# ioq3 code/qcommon/files.c: FS_InitFilesystem stops with a fatal error unless
# the active game directory has a readable default.cfg.
REQUIRED_CONFIG_FILE = "default.cfg"

# ioq3 code/game/g_bot.c: G_SpawnBots' own cadence, BOT_BEGIN_DELAY_BASE plus
# BOT_BEGIN_DELAY_INCREMENT per bot. The same values WP4 derives.
BOT_BEGIN_DELAY_BASE_MS = 2000
BOT_BEGIN_DELAY_INCREMENT_MS = 1500
BOT_SKILL_RANGE = (1, 5)

# ioq3 code/game/bg_public.h: GT_FFA.
FFA_GAMETYPE = "0"

# ioq3 code/server/sv_main.c SV_MasterHeartbeat: only "dedicated 2" registers
# with the public master servers, which WP5 lists as an explicit non-goal.
LAN_DEDICATED = "1"

# ioq3 code/qcommon/net_ip.c: NET_ENABLEV4 alone. The census observes exactly
# one address family.
IPV4_ONLY = "1"

CVAR_NAME = re.compile(r"\A[a-z][A-Za-z0-9_]*\Z")
MAP_NAME = re.compile(r"\A[a-z0-9][a-z0-9_]*\Z")
BOT_NAME = re.compile(r"\A[A-Za-z][A-Za-z0-9_]*\Z")
BINARY_NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]*\Z")
ABSOLUTE_DIRECTORY = re.compile(r"\A(?:/[A-Za-z0-9_.-]+)+\Z")

# ioq3 code/sys/sys_main.c:838-839: main() sets the install path from
# DEFAULT_BASEDIR, which on Linux is Sys_BinaryPath() (:739-747) — the
# directory of the executable, not the working directory. fs_basepath is
# therefore wherever the binary sits, so the game directory has to sit beside
# it. `profile.gameDirectory` is that one directory, and the profile has no
# second path to keep in step with it.

# The QVMs each side actually loads. The dedicated server runs the game module;
# the client runs the client game and the user interface. Shipping the other
# side's modules would put bytes into the image that nothing there loads.
SERVER_QVMS = ("qagame",)
CLIENT_QVMS = ("cgame", "ui")

QVM_ARTIFACT = "baseq3/vm/{module}.qvm"

# A staged tree becomes image content, so its modes are part of the artifact
# rather than a property of whoever ran the staging. Files are readable and not
# executable; directories are traversable.
STAGED_FILE_MODE = 0o644
STAGED_DIRECTORY_MODE = 0o755


class ArenaServerError(ValueError):
    """A native server or client set that may not be built or run."""


def _fail(path: str, message: str) -> None:
    raise ArenaServerError(f"{path}: {message}")


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


def _load_json(path: Path, what: str) -> Any:
    if not path.is_file():
        _fail(what, f"{path} does not exist")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _fail(what, f"is not valid JSON: {error}")
    return None


def expected_server_arguments(profile: dict[str, Any]) -> list[str]:
    """The one derivation of the dedicated server's command line.

    ``+set`` lines are read by ioq3's Com_StartupVariable before anything else
    runs and again after Com_ExecuteCfg, and the remaining lines are executed
    from the command buffer in order (code/qcommon/common.c), so ``+map`` must
    precede every ``+addbot``. ``addbot`` is not an engine command:
    Cmd_ExecuteString forwards it to the running game module's Svcmd_AddBot_f
    (code/qcommon/cmd.c, code/game/g_bot.c), which is why the map has to exist
    first.
    """
    arguments: list[str] = []
    for name in sorted(profile["cvars"]):
        arguments += ["+set", name, profile["cvars"][name]]
    arguments += ["+map", profile["map"]]
    for index, bot in enumerate(profile["bots"]):
        delay = BOT_BEGIN_DELAY_BASE_MS + index * BOT_BEGIN_DELAY_INCREMENT_MS
        arguments += ["+addbot", bot["name"], str(bot["skill"]), "free", str(delay)]
    return arguments


def expected_client_arguments(profile: dict[str, Any]) -> list[str]:
    """The one derivation of the native test client's command line.

    The connect target is deliberately absent: it is the address of a container
    that does not exist until the census runs, so it is supplied at run time and
    never committed.
    """
    cvars = profile["client"]["cvars"]
    arguments: list[str] = []
    for name in sorted(cvars):
        arguments += ["+set", name, cvars[name]]
    return arguments


def _validate_cvars(record: dict[str, Any], path: str) -> None:
    cvars = _object(record, path)
    if not cvars:
        _fail(path, "must not be empty")
    for name in cvars:
        if not CVAR_NAME.fullmatch(name):
            _fail(f"{path}.{name}", "is not a cvar name")
        _string(cvars[name], f"{path}.{name}")


def _validate_bots(profile: dict[str, Any]) -> None:
    bots = _array(profile.get("bots"), "profile.bots")
    if not bots:
        _fail("profile.bots", "must declare at least one bot")
    names = []
    for index, entry in enumerate(bots):
        bot = _object(entry, f"profile.bots[{index}]")
        _exact_keys(bot, ("name", "skill"), f"profile.bots[{index}]")
        name = _string(bot["name"], f"profile.bots[{index}].name")
        if not BOT_NAME.fullmatch(name):
            _fail(f"profile.bots[{index}].name", f"'{name}' is not a bot name")
        skill = bot["skill"]
        if not isinstance(skill, int) or isinstance(skill, bool):
            _fail(f"profile.bots[{index}].skill", "must be an integer")
        if not BOT_SKILL_RANGE[0] <= skill <= BOT_SKILL_RANGE[1]:
            _fail(
                f"profile.bots[{index}].skill",
                f"must be within {BOT_SKILL_RANGE}",
            )
        names.append(name)
    if len(set(names)) != len(names):
        _fail("profile.bots", "must not name one bot twice")


def _validate_config_files(profile: dict[str, Any], repo_root: Path) -> None:
    entries = _array(profile.get("configFiles"), "profile.configFiles")
    roles = []
    for index, entry in enumerate(entries):
        record = _object(entry, f"profile.configFiles[{index}]")
        _exact_keys(record, ("role", "served", "source"), f"profile.configFiles[{index}]")
        role = _string(record["role"], f"profile.configFiles[{index}].role")
        if role not in CONFIG_ROLES:
            _fail(f"profile.configFiles[{index}].role", f"must be one of {CONFIG_ROLES}")
        roles.append(role)
        served = _string(record["served"], f"profile.configFiles[{index}].served")
        if served != REQUIRED_CONFIG_FILE:
            _fail(
                f"profile.configFiles[{index}].served",
                f"the engine requires exactly {REQUIRED_CONFIG_FILE}",
            )
        source = _string(record["source"], f"profile.configFiles[{index}].source")
        resolved = _config_source(repo_root, source)
        if not resolved.is_file():
            _fail(f"profile.configFiles[{index}].source", f"{resolved} does not exist")
    if sorted(roles) != sorted(CONFIG_ROLES):
        _fail("profile.configFiles", f"must supply one config per role {CONFIG_ROLES}")


def _config_source(repo_root: Path, source: str) -> Path:
    """Resolve a configuration source, refusing anything outside the checkout."""
    candidate = (repo_root / CONFIG_SOURCE_DIRECTORY / source).resolve()
    root = repo_root.resolve()
    if root != candidate and root not in candidate.parents:
        _fail("profile.configFiles[].source", f"{source} leaves the repository")
    return candidate


def _validate_against_browser_profile(
    profile: dict[str, Any], browser: dict[str, Any]
) -> None:
    """Bind the native profile to the browser slice it has to match."""
    for key in ("basegame", "map", "package", "playerModel"):
        if profile[key] != browser.get(key):
            _fail(
                f"profile.{key}",
                f"must equal the browser slice's {key} '{browser.get(key)}'",
            )
    browser_cvars = _object(browser.get("cvars"), "browser.cvars")
    for name in ("com_basegame", "fraglimit", "g_gametype", "sv_pure", "timelimit"):
        if profile["cvars"].get(name) != browser_cvars.get(name):
            _fail(
                f"profile.cvars.{name}",
                f"must equal the browser slice's value '{browser_cvars.get(name)}'",
            )
    browser_bots = [bot["name"] for bot in _array(browser.get("bots"), "browser.bots")]
    if [bot["name"] for bot in profile["bots"]] != browser_bots:
        _fail("profile.bots", "must be the browser slice's bots, in the same order")


def _validate_against_recipe(
    profile: dict[str, Any], recipe: dict[str, Any], repo_root: Path
) -> None:
    """Bind the native profile to the audited content pack it starts."""
    recipe_profile = _object(recipe.get("profile"), "recipe.profile")
    package = _object(recipe.get("package"), "recipe.package")
    if profile["package"] != package.get("id"):
        _fail(
            "profile.package", f"must equal the recipe package id '{package.get('id')}'"
        )
    try:
        fragment = load_map_fragment(repo_root, profile["map"], CONTENT_MANIFEST)
    except ArenaRuntimeError as error:
        _fail("profile.map", str(error))
    arena = fragment["arena"]
    if arena.get("type") != "ffa":
        _fail("recipe.profile.arena.type", "the native profile only starts an FFA arena")
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
        if bot["name"] not in recipe_bots:
            _fail("profile.bots", f"'{bot['name']}' is not a bot the pack packages")


def load_profile(repo_root: Path) -> dict[str, Any]:
    """Read, validate and cross-check the committed native profile."""
    profile = _object(_load_json(repo_root / PROFILE_SOURCE, PROFILE_SOURCE), "profile")
    _exact_keys(profile, PROFILE_KEYS, "profile")
    if profile.get("formatVersion") != 1:
        _fail("profile.formatVersion", "must be 1")
    _array(profile.get("$comment"), "profile.$comment")
    for key in ("basegame", "map", "package", "playerModel", "serverBinary"):
        _string(profile.get(key), f"profile.{key}")
    if not MAP_NAME.fullmatch(profile["map"]):
        _fail("profile.map", f"'{profile['map']}' is not a map name")
    if not MAP_NAME.fullmatch(profile["basegame"]):
        _fail("profile.basegame", f"'{profile['basegame']}' is not a game directory")
    if profile["basegame"] == IOQ3_RETAIL_BASEGAME:
        _fail(
            "profile.basegame",
            "must not be ioquake3's own base game: FS_CheckPak0 would then require "
            "the retail paks instead of selecting standalone operation",
        )
    if not BINARY_NAME.fullmatch(profile["serverBinary"]):
        _fail("profile.serverBinary", "is not a binary name")
    game_directory = _string(profile.get("gameDirectory"), "profile.gameDirectory")
    if not ABSOLUTE_DIRECTORY.fullmatch(game_directory):
        _fail("profile.gameDirectory", "must be an absolute image path")

    port = profile.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        _fail("profile.port", "must be an unprivileged UDP port")

    # The number of per-package copyright files the pinned runtime base carries.
    # Comparing the built image with the base proves nothing on its own — both
    # sides would agree if both silently lost the same files — so the expected
    # count is pinned here and required of the base itself.
    copyright_files = profile.get("runtimeBaseCopyrightFiles")
    if (
        not isinstance(copyright_files, int)
        or isinstance(copyright_files, bool)
        or copyright_files < 1
    ):
        _fail(
            "profile.runtimeBaseCopyrightFiles",
            "must be the positive number of copyright files the runtime base carries",
        )

    _validate_cvars(profile.get("cvars"), "profile.cvars")
    client = _object(profile.get("client"), "profile.client")
    _exact_keys(client, ("cvarNotes", "cvars"), "profile.client")
    _validate_cvars(client.get("cvars"), "profile.client.cvars")
    _validate_bots(profile)
    _validate_config_files(profile, repo_root)

    for label, cvars, notes in (
        ("profile", profile["cvars"], _object(profile.get("cvarNotes"), "profile.cvarNotes")),
        (
            "profile.client",
            client["cvars"],
            _object(client.get("cvarNotes"), "profile.client.cvarNotes"),
        ),
    ):
        if sorted(notes) != sorted(cvars):
            _fail(f"{label}.cvarNotes", "must explain exactly the cvars it sets")
        for name in notes:
            if len(_string(notes[name], f"{label}.cvarNotes.{name}")) < 20:
                _fail(f"{label}.cvarNotes.{name}", "must state why the cvar is set")

    if profile["cvars"].get("com_basegame") != profile["basegame"]:
        _fail("profile.cvars.com_basegame", "must be the profile's game directory")
    if profile["cvars"].get("g_gametype") != FFA_GAMETYPE:
        _fail("profile.cvars.g_gametype", "must be GT_FFA for this profile")
    if profile["cvars"].get("dedicated") != LAN_DEDICATED:
        _fail(
            "profile.cvars.dedicated",
            "must be 1: 'dedicated 2' registers with the public master servers",
        )
    if profile["cvars"].get("net_enabled") != IPV4_ONLY:
        _fail("profile.cvars.net_enabled", "must be 1 so the census sees only IPv4")
    if profile["cvars"].get("net_port") != str(port):
        _fail("profile.cvars.net_port", "must equal the profile port")
    if profile["cvars"].get("sv_pure") != "0":
        _fail(
            "profile.cvars.sv_pure",
            "must be 0: FS_FindVM only finds a loose vm/*.qvm while fs_numServerPaks is 0",
        )
    if client["cvars"].get("com_basegame") != profile["basegame"]:
        _fail("profile.client.cvars.com_basegame", "must be the profile's game directory")
    if client["cvars"].get("net_enabled") != IPV4_ONLY:
        _fail("profile.client.cvars.net_enabled", "must be 1 so the census sees only IPv4")
    if client["cvars"].get("cl_allowDownload") != "0":
        _fail(
            "profile.client.cvars.cl_allowDownload",
            "must be 0: WP5 has to show that no media download is attempted",
        )
    for name in ("headmodel", "model"):
        if client["cvars"].get(name) != profile["playerModel"]:
            _fail(
                f"profile.client.cvars.{name}",
                "must be the profile's player presentation: ioq3 defaults it to "
                "'sarge', which is retail Quake III data, and CG_NewClientInfo "
                "then drops the client when DEFAULT_MODEL also fails to register",
            )

    browser = _object(
        _load_json(repo_root / BROWSER_PROFILE_SOURCE, BROWSER_PROFILE_SOURCE),
        "browser",
    )
    _validate_against_browser_profile(profile, browser)
    recipe = _object(_load_json(repo_root / RECIPE_SOURCE, RECIPE_SOURCE), "recipe")
    _validate_against_recipe(profile, recipe, repo_root)

    for key, expected in (
        ("serverArguments", expected_server_arguments(profile)),
        ("clientArguments", expected_client_arguments(profile)),
    ):
        committed = _array(profile.get(key), f"profile.{key}")
        if committed != expected:
            _fail(
                f"profile.{key}",
                "must be exactly the derivation of the profile's declarative fields",
            )

    profile["_manifests"] = {
        "content": manifest_index(
            _load_json(repo_root / CONTENT_MANIFEST, CONTENT_MANIFEST), CONTENT_MANIFEST
        ),
        "engine": manifest_index(
            _load_json(repo_root / ENGINE_MANIFEST, ENGINE_MANIFEST), ENGINE_MANIFEST
        ),
    }
    return profile


def _content_pack_paths(repo_root: Path) -> list[str]:
    """Every archive the server image carries: the base plus one per map.

    The dedicated server holds the whole supported map set and downloads
    nothing, so it carries every archive the content manifest names. Which of
    them a given rotation touches is a launch-time question, not a packaging
    one.
    """
    recipe = _object(_load_json(repo_root / RECIPE_SOURCE, RECIPE_SOURCE), "recipe")
    template = _string(recipe.get("mapPackTemplate"), "recipe.mapPackTemplate")
    manifest = _object(
        _load_json(repo_root / CONTENT_MANIFEST, CONTENT_MANIFEST), CONTENT_MANIFEST
    )
    try:
        maps = manifest_map_inputs(manifest, CONTENT_MANIFEST)
    except ArenaRuntimeError as error:
        _fail(CONTENT_MANIFEST, str(error))
    return sorted(
        [_string(recipe.get("basePackPath"), "recipe.basePackPath")]
        + [template.format(map=name) for name in maps]
    )


def _game_tree(
    repo_root: Path, profile: dict[str, Any], modules: tuple[str, ...], config_role: str
) -> dict[str, dict[str, Any]]:
    """The game directory one side needs, as ``relative path -> expected entry``."""
    basegame = profile["basegame"]
    files: dict[str, dict[str, Any]] = {}
    for pack_path in _content_pack_paths(repo_root):
        entry = profile["_manifests"]["content"][pack_path]
        files[f"{basegame}/{PurePosixPath(pack_path).name}"] = {
            "kind": "artifact",
            "manifest": "content",
            "artifactPath": pack_path,
            "sha256": entry["sha256"],
            "size": entry["size"],
        }
    for module in modules:
        artifact_path = QVM_ARTIFACT.format(module=module)
        entry = profile["_manifests"]["engine"][artifact_path]
        files[f"{basegame}/vm/{module}.qvm"] = {
            "kind": "artifact",
            "manifest": "engine",
            "artifactPath": artifact_path,
            "sha256": entry["sha256"],
            "size": entry["size"],
        }
    config = next(
        item for item in profile["configFiles"] if item["role"] == config_role
    )
    files[f"{basegame}/{config['served']}"] = {
        "kind": "config",
        "source": _config_source(repo_root, config["source"]),
    }
    return files


def server_tree_files(
    repo_root: Path, profile: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    return _game_tree(repo_root, profile, SERVER_QVMS, "server")


def client_tree_files(
    repo_root: Path, profile: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    return _game_tree(repo_root, profile, CLIENT_QVMS, "client")


def server_binary_path(profile: dict[str, Any]) -> str:
    """The absolute image path of the dedicated server binary.

    It is inside `gameDirectory` because ioq3 derives fs_basepath from the
    executable's own directory (code/sys/sys_main.c:838-839 with DEFAULT_BASEDIR
    at :739-747), so a binary anywhere else would need a runtime fs_basepath
    override that the profile would then have to keep in step.
    """
    return f"{profile['gameDirectory'].rstrip('/')}/{profile['serverBinary']}"


def image_content_paths(profile: dict[str, Any], tree: dict[str, Any]) -> list[str]:
    """Every path the server image adds on top of the pinned runtime base.

    Manifest paths are relative, so the leading slash of the absolute image
    location is stripped rather than kept as a second spelling of the same path.
    """
    prefix = profile["gameDirectory"].lstrip("/")
    return sorted(
        [server_binary_path(profile).lstrip("/")]
        + [f"{prefix}/{name}" for name in tree]
    )


def stage_tree(
    repo_root: Path,
    target: Path,
    files: dict[str, dict[str, Any]],
    *,
    engine_dir: Path,
    content_dir: Path,
) -> list[dict[str, Any]]:
    """Assemble a game tree, refusing any artifact that is not the committed one."""
    directories = {"engine": engine_dir, "content": content_dir}
    for name, directory in directories.items():
        if not directory.is_dir():
            _fail(f"{name} directory", f"{directory} does not exist; build it first")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, mode=STAGED_DIRECTORY_MODE)
    target.chmod(STAGED_DIRECTORY_MODE)

    verified: list[dict[str, Any]] = []
    for relative, entry in sorted(files.items()):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        for parent in reversed(destination.parents):
            if target in parent.parents or parent == target:
                parent.chmod(STAGED_DIRECTORY_MODE)
        if entry["kind"] == "artifact":
            source = directories[entry["manifest"]] / entry["artifactPath"]
            if not source.is_file():
                _fail(relative, f"{source} does not exist; build it first")
            size = source.stat().st_size
            digest = file_sha256(source)
            if digest != entry["sha256"] or size != entry["size"]:
                _fail(
                    relative,
                    f"{source} is not the committed {entry['manifest']} artifact "
                    f"(expected sha256:{entry['sha256']} {entry['size']} bytes, "
                    f"got sha256:{digest} {size} bytes)",
                )
            verified.append(
                {
                    "path": relative,
                    "sha256": digest,
                    "size": size,
                    "manifest": entry["manifest"],
                    "artifactPath": entry["artifactPath"],
                }
            )
        else:
            source = entry["source"]
            if not source.is_file():
                _fail(relative, f"{source} does not exist")
        shutil.copyfile(source, destination)
        destination.chmod(STAGED_FILE_MODE)
    verify_staged_tree(target, files)
    return verified


def verify_staged_tree(target: Path, files: dict[str, dict[str, Any]]) -> list[str]:
    """Re-read a staged tree and require exactly the expected files and digests."""
    if not target.is_dir():
        _fail("staged tree", f"{target} does not exist")
    present = set()
    for path in sorted(target.rglob("*")):
        if path.is_dir():
            if path.is_symlink():
                _fail("staged tree", f"{path.relative_to(target)} is a symlinked directory")
            if path.stat().st_mode & 0o7777 != STAGED_DIRECTORY_MODE:
                _fail(
                    "staged tree",
                    f"{path.relative_to(target)} is not mode {STAGED_DIRECTORY_MODE:04o}",
                )
            continue
        if path.is_symlink() or not path.is_file():
            _fail("staged tree", f"{path.relative_to(target)} is not a regular file")
        if path.stat().st_mode & 0o7777 != STAGED_FILE_MODE:
            _fail(
                "staged tree",
                f"{path.relative_to(target)} is not mode {STAGED_FILE_MODE:04o}",
            )
        present.add(path.relative_to(target).as_posix())
    unknown = sorted(present - set(files))
    if unknown:
        _fail("staged tree", f"contains files the profile does not declare: {unknown}")
    missing = sorted(set(files) - present)
    if missing:
        _fail("staged tree", f"is missing declared files: {missing}")
    for relative, entry in sorted(files.items()):
        path = target / relative
        if entry["kind"] == "artifact":
            if file_sha256(path) != entry["sha256"] or path.stat().st_size != entry["size"]:
                _fail(f"staged {relative}", "does not match the committed manifest identity")
        elif file_sha256(path) != file_sha256(entry["source"]):
            _fail(f"staged {relative}", f"differs from {entry['source']}")
    return sorted(files)
