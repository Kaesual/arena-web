# SPDX-License-Identifier: GPL-2.0-or-later
"""Automated pre-acceptance of the offline vertical slice.

This drives the exact WP0 acceptance browser against a clean local serve of the
staged runtime set and records what happened: load and frame timing, the
engine's own console output, the browser console, every network request the
page made, the runtime artifact identities and screenshots.

What it is **not** is the work package's witnessed acceptance. A person at the
real WP0 desktop still has to move, look, fire, take and deal damage, score,
restart a session, lose and regain focus, go fullscreen and hear audio. This
run exists so that the witnessed round starts from a client that is already
known to load, boot, enter the map, keep its declared identities and produce no
console defect — and so that a regression is caught without a human.
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import re
import shutil
import socketserver
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena_runtime import (  # noqa: E402
    ArenaRuntimeError,
    file_sha256,
    offline_map_arguments,
    load_profile,
    served_files,
    stage,
    verify_staged,
)
from browser_session import (  # noqa: E402
    BrowserSessionError,
    ChromeProcess,
    DevToolsSession,
    wait_until,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Engine-side defect classes. Every pattern is a literal Com_Printf/Com_Error
# format of the pinned ioquake3 tree; the source of each is named beside it, so
# a reader can check that the classification is the engine's own vocabulary and
# not this driver's guess.
ENGINE_DEFECT_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "missing-asset",
        "renderergl2/tr_shader.c R_FindImageFile; client/snd_codec.c S_CodecLoad;"
        " client/snd_dma.c S_FindName; client/snd_openal.c S_AL_BufferUseDefault;"
        " cgame/cg_players.c CG_RegisterClientModelname and CG_ParseAnimationFile;"
        " renderergl2/tr_model.c R_RegisterMD3 (that one is _DEBUG-only in the"
        " pinned tree and therefore silent in an accepted Release build)",
        re.compile(
            r"R_FindImageFile could not find"
            r"|could not find .* - using default"
            r"|Failed to (?:load|open) sound"
            r"|Using default sound for"
            r"|Failed to load model file"
            r"|Failed to load skin file:"
            r"|Failed to load animation file"
            r"|R_RegisterMD3: couldn't load",
        ),
    ),
    (
        "qvm-rejection",
        "qcommon/vm.c VM_LoadQVM and VM_Create",
        re.compile(
            r"has bad header"
            r"|does not have a recognisable"
            r"|not matching after"
            r"|Couldn't open VM file"
            r"|VM_Create: (?:bad parms|no free)",
        ),
    ),
    (
        "renderer-fatal",
        "renderergl2/tr_init.c GL_CheckErrors; sdl/sdl_glimp.c GLimp_Init",
        re.compile(
            r"GL_CheckErrors:|could not load OpenGL subsystem|GLimp_Init\(\) - ",
        ),
    ),
    (
        "engine-error",
        "qcommon/common.c Com_Error for ERR_DROP, which prefixes 'ERROR: '."
        " ERR_FATAL does not: Sys_Error prints the bare message through"
        " Sys_ErrorDialog (sys/sys_unix.c:975), so the known fatal texts are"
        " listed literally and the engine-kept-running check below is the real"
        " catch-all",
        re.compile(
            r"^\s*ERROR: "
            r"|recursive error after:"
            r"|Couldn't load default\.cfg"
            r"|Quake 3 data files are missing"
            r"|Failed to load renderer"
            r"|Couldn't load maps/",
        ),
    ),
)

# References the engine reports missing that this profile does not need, each
# with the reason it is acceptable. They are recorded in the evidence, never
# silently dropped, and anything the engine reports missing that is *not* on
# this list fails the run. The list is deliberately literal: a wildcard here
# would hide the next real gap.
ACCEPTED_ENGINE_NOTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"Failed to (?:load|open) sound music/sonic5\.wav!"),
        "oa_pvomit's worldspawn names a music track no OpenArena release ships; "
        "WP3 already accepted it as a dangling upstream reference",
    ),
    (
        re.compile(r"Failed to (?:load|open) sound sound/player/skelebot/taunt\.wav"),
        "OpenArena ships no taunt for the skelebot voice set — the packaged set is "
        "complete, upstream simply has 12 of the 13 custom sounds",
    ),
    (
        re.compile(
            r"Failed to (?:load|open) sound sound/player/sarge/taunt\.wav"
            r"|Using default sound for sound/player/(?:skelebot|sarge)/taunt\.wav"
            r"|could not find sound/player/(?:skelebot|sarge)/taunt\.wav"
            r" - using default"
        ),
        "the taunt fallback to DEFAULT_MODEL (ioq3 code/cgame/cg_players.c "
        "CG_LoadClientInfo); 'sarge' is retail Quake III data and is not packaged",
    ),
    (
        re.compile(
            r"R_FindImageFile could not find 'gfx/fx/flares/blur\.tga' in shader "
            r"'flareShader'"
        ),
        "tr.flareShader is registered by the renderer itself (ioq3 "
        "code/renderergl2/tr_shader.c R_InitShaders), outside the QVM references "
        "WP3's closure reads, and r_flares defaults to 0 so no flare is drawn",
    ),
    (
        re.compile(
            r"R_FindImageFile could not find 'textures/flares/flarey\.tga' in shader "
            r"'sun'|Shader sun has a stage with no image"
        ),
        "tr.sunShader is registered by the renderer itself (ioq3 "
        "code/renderergl2/tr_shader.c R_InitShaders) and this profile's map has no "
        "sun flare",
    ),
    (
        re.compile(
            r"R_FindImageFile could not find 'textures/sfx/logo256\.tga' in shader "
            r"'console'"
        ),
        "cls.consoleShader is registered by the client (ioq3 "
        "code/client/cl_main.c CL_InitRenderer); it is the drop-down console "
        "backdrop, not gameplay content",
    ),
    # The entries below belong to the published map set rather than to the one
    # map this profile starts, so a rotation change cannot turn a known and
    # reasoned upstream gap into an acceptance failure. Each was observed in a
    # native client that actually loaded the map it belongs to, not predicted:
    # the exact reference is the anchor.
    #
    # There are three phrasings for one missing sound, and which of them appears
    # depends on the layer and the backend, not on the fact:
    #   'Failed to load|open sound <name>!'     snd_codec.c S_CodecGetSound,
    #                                           under either backend;
    #   'could not find <name> - using default' snd_dma.c S_RegisterSound;
    #   'Using default sound for <name>'        snd_openal.c S_AL_BufferUseDefault.
    # A worldspawn music value reaches only the first, because neither backend's
    # StartBackgroundTrack goes through S_RegisterSound: S_AL_StartBackgroundTrack
    # calls S_CodecOpenStream directly and S_Base_StartBackgroundTrack does the
    # same through S_OpenBackgroundStream. Such a note therefore carries the
    # codec spelling *only* - the other two would accept a line that cannot come
    # from the mechanism the note reasons about. (The dma path prints a fourth
    # line beside the codec one, snd_dma.c's "couldn't open music file <name>";
    # it is unclassified because no ENGINE_DEFECT_PATTERNS entry matches it, so
    # adding a "couldn't open" alternative there would need a note per music
    # reference.) An entity or gamecode sound
    # reaches S_RegisterSound and can produce any of the three, so its note must
    # list all three - and the third is not hypothetical here: USE_OPENAL
    # is on and only USE_OPENAL_DLOPEN is disabled for Emscripten
    # (ioq3/CMakeLists.txt, cmake/platforms/emscripten.cmake), and s_useOpenAL
    # defaults to "1" (client/snd_main.c), so the acceptance browser runs the
    # OpenAL backend. A native run on the dummy audio driver falls back to dma
    # and shows the *other* phrasing, which is why a native log alone cannot
    # tell you which of them the acceptance will see.
    # sound_notes_are_complete() in tests/test_arena_runtime.py holds this
    # rule for every sound reference the published set accepts.
    (
        re.compile(
            r"Failed to (?:load|open) sound sound/misc/windfly\.wav"
            r"|could not find sound/misc/windfly\.wav - using default"
            r"|Using default sound for sound/misc/windfly\.wav"
        ),
        "SP_target_push registers sound/misc/windfly.wav unless the entity "
        "carries the bouncepad spawnflag (ioq3 code/game/g_trigger.c), and no "
        "OpenArena release ships that file; the recipe accepts it as a dangling "
        "reference of the gamecode's own closure. It is reachable from any map "
        "with a non-bouncepad target_push, which oa_pvomit does not have and "
        "czest1tourney does",
    ),
    (
        re.compile(r"Failed to (?:load|open) sound music/OA09\.ogg!"),
        "the worldspawn music key of czest1dm and of oa_koth1 names a track no "
        "pinned OpenArena release ships; both map fragments accept it and a "
        "missing track is silence, not a failure",
    ),
    (
        re.compile(
            r"Failed to (?:load|open) sound sound/ambient/sparks\.ogg\.wav"
            r"|could not find sound/ambient/sparks\.ogg\.wav - using default"
            r"|Using default sound for sound/ambient/sparks\.ogg\.wav"
        ),
        "an am_underworks2 entity names sound/ambient/sparks.ogg, which no "
        "pinned release ships; the engine appends .wav to the name it reports, "
        "and the map fragment accepts the reference",
    ),
    (
        re.compile(
            r"R_FindImageFile could not find "
            r"'models/mapobjects/cosmoflash/tele4_frame_glow\.jpg' in shader "
            r"'models/mapobjects/cosmoflash/tele4_frame'"
        ),
        "a shipped OpenArena shader names an image no pinned archive provides. "
        "It defaults one mapobject shader on am_underworks2, 26 of that map's "
        "4,197 faces; the map fragment accepts the reference and the audit "
        "records the measurement",
    ),
    # WP-F batch 2. Same rule as the block above: one entry per exact
    # reference, each belonging to a map this release publishes, each observed
    # in the native run that loaded it.
    (
        re.compile(r"Failed to (?:load|open) sound music/OA10\.ogg!"),
        "sleekgrinder's worldspawn music key names a track no pinned OpenArena "
        "release ships; the map fragment accepts it and a missing track is "
        "silence, not a failure",
    ),
    (
        re.compile(r"Failed to (?:load|open) sound music/OA11\.ogg!"),
        "pul1duel-oa's worldspawn music key names a track no pinned OpenArena "
        "release ships; the map fragment accepts it and a missing track is "
        "silence, not a failure",
    ),
    (
        re.compile(r"Failed to (?:load|open) sound music/OA03\.ogg!"),
        "oa_shouse's worldspawn music key names a track no pinned OpenArena "
        "release ships; the map fragment accepts it and a missing track is "
        "silence, not a failure",
    ),
    (
        re.compile(r"Failed to (?:load|open) sound music/sonic6!"),
        "slimefac's worldspawn music key names music/sonic6 without a file "
        "extension; S_CodecGetSound then probes wav, ogg and opus against that "
        "stem (ioq3 code/client/snd_codec.c) and no pinned OpenArena release "
        "ships any of them, so the name the engine reports is the bare stem",
    ),
    (
        re.compile(
            r"Failed to (?:load|open) sound sound/world/fireloud\.wav"
            r"|could not find sound/world/fireloud\.wav - using default"
            r"|Using default sound for sound/world/fireloud\.wav"
        ),
        "five func_bobbing entities of oa_shouse name sound/world/fireloud.wav "
        "in their 'noise' key and no pinned OpenArena release ships it; "
        "InitMover registers the value as written (ioq3 code/game/g_mover.c), "
        "the map fragment accepts it, and a missing looping sound is silence",
    ),
    (
        re.compile(
            r"R_FindImageFile could not find 'textures/cosmo_sfx/diamond_b\.tga' "
            r"in shader 'textures/cosmo_sfx/diamond_blue'"
        ),
        "a shipped OpenArena shader names an image no pinned archive provides. "
        "It defaults one shader on oa_koth1, 31 of that map's 2,200 faces; the "
        "map fragment accepts the reference and the audit records the "
        "measurement",
    ),
    # WP-F batch 3, the Quake 1 conversion family. Every entry here is a
    # worldspawn `music` key -- checked in the entity lump of each BSP, not
    # inferred from the closure, which reports a worldspawn key and an entity
    # `noise` key under the same origin. That check is load-bearing rather than
    # thorough: cgame/cg_servercmds.c registers *any* CS_SOUNDS string through
    # S_RegisterSound whatever directory it names, so a `music/...` path in an
    # entity `noise` key would reach the two S_RegisterSound spellings, and
    # nothing in a fragment records which key a reference came from.
    #
    # The family's other acceptance class, `textures/NULL`, needs no note at
    # all, and the reason is narrower than "PRINT_DEVELOPER only":
    # R_LoadShaders byte-swaps flags, so a shader-lump entry reaches
    # R_FindShader through ShaderForShaderNum alone, and no surface of the four
    # maps indexes it. The fog lump and R_LoadEntities' `remapshader` keys can
    # register a name independently -- they are separate lumps, not consumers
    # of this one -- and neither names it either. The fragments record the
    # measurement.
    (
        re.compile(r"Failed to (?:load|open) sound music/sonic5!"),
        "ce1m7's worldspawn music key names music/sonic5 without a file "
        "extension, so the engine reports the bare stem after probing wav, ogg "
        "and opus against it. oa_pvomit names the same track *with* '.wav' and "
        "is reported under that name, which is why this release accepts two "
        "spellings of one absent track rather than one",
    ),
    (
        re.compile(r"Failed to (?:load|open) sound music/sonic6\.ogg!"),
        "oa_dm1's worldspawn music key names music/sonic6.ogg; S_CodecGetSound "
        "tries the ogg codec, then the stem against wav and opus, and no "
        "pinned OpenArena release ships any of them. slimefac names the same "
        "stem bare, so this is the second spelling of that absence too",
    ),
    (
        re.compile(r"Failed to (?:load|open) sound music/sonic3\.ogg!"),
        "the worldspawn music key of oa_dm5 and of oa_dm6 names a track no "
        "pinned OpenArena release ships; both map fragments accept it and a "
        "missing track is silence, not a failure",
    ),
    (
        re.compile(
            r"Failed to (?:load|open) sound music/fla22k_04_intro\.ogg!"
            r"|Failed to (?:load|open) sound music/fla22k_04_loop\.ogg!"
        ),
        "oa_dm2's worldspawn music key names two tracks in one value, which "
        "CG_StartMusic splits into intro and loop with two COM_Parse calls "
        "(ioq3 code/cgame/cg_main.c). Neither is shipped, and how many lines "
        "that produces depends on the backend: S_AL_StartBackgroundTrack opens "
        "the intro and then the loop, so the OpenAL client this acceptance runs "
        "reports both names, while the dma backend opens only the intro. One "
        "fragment entry, two names the engine can report",
    ),
    # WP-F batch 4, the last five maps. Only one of them reports anything:
    # aggressor's music track and every entity sound of the five resolve, and
    # oa_shine's one target_speaker names `*falling1.wav`, which
    # cg_servercmds.c does not register because the string starts with '*'.
    # That is not silence and not a property of the star: G_SoundIndex still
    # puts the name in CS_SOUNDS, the event reaches CG_CustomSound, and
    # cg_players.c answers it from the *activating client's own* sound set --
    # for the thirteen names in cg_customSoundNames, of which `*falling1.wav`
    # is one. For any other `*name` CG_CustomSound calls CG_Error and drops the
    # client, and the closure cannot tell the two apart because _add_bsp skips
    # every `*` value. Recorded as a closure gap in docs/wp3-content-closure.md.
    #
    # kaos2's second music name gets no note on purpose. Its `.wav` spelling is
    # a 'music' key on two func_door entities, and 'music' is not in the game's
    # spawn-field table: G_ParseField drops the key and SP_worldspawn is the
    # only reader of one (ioq3 code/game/g_spawn.c), so nothing indexes, opens
    # or reports that name. It is recorded as unreachable in
    # tests/test_arena_runtime.py, where a wrong claim fails the run, rather
    # than accepted here, where it would silently cover a line if the claim
    # were wrong.
    (
        re.compile(r"Failed to (?:load|open) sound music/fla22k_05!"),
        "kaos2's worldspawn music key names music/fla22k_05 without a file "
        "extension, so S_CodecGetSound probes wav, ogg and opus against the "
        "stem and reports the bare name. No pinned OpenArena release ships the "
        "track under any of them, the map fragment accepts it, and a missing "
        "track is silence",
    ),
)

BROWSER_ALLOWED_SCHEMES = ("blob:", "data:")

MOVEMENT_KEYS = (
    ("KeyW", "w", 87),
    ("KeyA", "a", 65),
    ("KeyS", "s", 83),
    ("KeyD", "d", 68),
)


class AcceptanceError(RuntimeError):
    """The automated pre-acceptance could not be carried out."""


@dataclass(frozen=True)
class Expectations:
    """Everything a run is scored against, read from this checkout."""

    files: frozenset[str]
    origin: str
    config_digests: dict[str, str]
    artifact_digests: dict[str, str]
    engine_arguments: tuple[str, ...]
    bot_names: tuple[str, ...]
    # The rotation this run opens the page for, the served archives it must
    # therefore fetch, and the published archives it must leave alone. The last
    # one is the point: a run that fetched everything would pass every other
    # check in this file.
    rotation: tuple[str, ...]
    rotation_parameter: str
    rotation_served: frozenset[str]
    rotation_excluded: frozenset[str]
    # The map the offline slice starts: the rotation's first entry as given.
    start_map: str


def pinned_browser_version(repo_root: Path) -> str:
    """The exact acceptance-browser version WP0 froze."""
    baseline = json.loads(
        (repo_root / "locks/baseline.json").read_text(encoding="utf-8")
    )
    for tool in baseline.get("tools", []):
        if tool.get("id") == "chrome-for-testing":
            return str(tool["version"])
    raise AcceptanceError("locks/baseline.json records no acceptance browser")


# --------------------------------------------------------------------------
# A local serve that is exactly the staged tree.
# --------------------------------------------------------------------------


class _AccessLoggingHandler(http.server.SimpleHTTPRequestHandler):
    access_log: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def send_response(self, code: int, message: str | None = None) -> None:
        type(self).access_log.append({"path": self.path, "status": int(code)})
        super().send_response(code, message)


class StaticServe:
    """A loopback static server over one directory, with its own access log."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.access_log: list[dict[str, Any]] = []
        handler_class = type(
            "ArenaHandler",
            (_AccessLoggingHandler,),
            {"access_log": self.access_log},
        )

        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = _Server(
            ("127.0.0.1", 0),
            lambda *arguments: handler_class(*arguments, directory=str(directory)),
        )
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> StaticServe:
        self._thread.start()
        return self

    def __exit__(self, *_exception: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# --------------------------------------------------------------------------
# Screenshot statistics: enough to tell a rendered scene from a blank canvas.
# --------------------------------------------------------------------------


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AcceptanceError("screenshot is not a PNG")
    chunks: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        chunks.append((kind, body))
        offset += 12 + length
        if kind == b"IEND":
            break
    return chunks


def png_pixel_statistics(data: bytes, *, sample_stride: int = 1) -> dict[str, Any]:
    """Decode a truecolour PNG and describe what is on it.

    The point is not image quality: it is to be able to say, from evidence
    rather than from a claim, that the canvas is not a single flat colour.
    """
    chunks = _png_chunks(data)
    header = next((body for kind, body in chunks if kind == b"IHDR"), None)
    if header is None:
        raise AcceptanceError("screenshot has no IHDR")
    width = int.from_bytes(header[0:4], "big")
    height = int.from_bytes(header[4:8], "big")
    depth = header[8]
    colour_type = header[9]
    interlace = header[12]
    if depth != 8 or colour_type not in (2, 6) or interlace != 0:
        raise AcceptanceError(
            f"unsupported PNG (depth {depth}, colour type {colour_type}, interlace {interlace})"
        )
    channels = 3 if colour_type == 2 else 4
    raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))

    stride = width * channels
    previous = bytearray(stride)
    colours: set[tuple[int, int, int]] = set()
    luminance_total = 0
    near_white = 0
    counted = 0
    offset = 0
    for _row in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset : offset + stride])
        offset += stride
        if filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                up = previous[index]
                up_left = previous[index - channels] if index >= channels else 0
                estimate = left + up - up_left
                distance_left = abs(estimate - left)
                distance_up = abs(estimate - up)
                distance_up_left = abs(estimate - up_left)
                if distance_left <= distance_up and distance_left <= distance_up_left:
                    predictor = left
                elif distance_up <= distance_up_left:
                    predictor = up
                else:
                    predictor = up_left
                line[index] = (line[index] + predictor) & 0xFF
        elif filter_type != 0:
            raise AcceptanceError(f"unknown PNG filter {filter_type}")
        for column in range(0, width, sample_stride):
            base = column * channels
            pixel = (line[base], line[base + 1], line[base + 2])
            colours.add(pixel)
            luminance_total += (
                pixel[0] * 299 + pixel[1] * 587 + pixel[2] * 114
            ) // 1000
            if pixel[0] > 245 and pixel[1] > 245 and pixel[2] > 245:
                near_white += 1
            counted += 1
        previous = line

    return {
        "width": width,
        "height": height,
        "sampledPixels": counted,
        "distinctColours": len(colours),
        "meanLuminance": round(luminance_total / counted, 2) if counted else 0.0,
        "nearWhiteFraction": round(near_white / counted, 4) if counted else 0.0,
        "bytes": len(data),
    }


# --------------------------------------------------------------------------
# Engine log classification.
# --------------------------------------------------------------------------


# Quake III colour codes are '^' followed by any character other than '^'
# (ioq3 code/qcommon/q_shared.h, Q_IsColorString). The engine prints
# "<netname>^7 entered the game", so a name comparison has to see through them.
COLOR_CODE = re.compile(r"\^[^^]")


def strip_color_codes(line: str) -> str:
    return COLOR_CODE.sub("", line)


def bots_from_engine_log(lines: list[str], bot_names: tuple[str, ...]) -> set[str]:
    """Which of the configured bots the engine reported as having joined.

    Deliberately name-anchored: ioq3 code/game/g_client.c:1026 prints the same
    sentence for every client, the local player included, so only an exact
    "<bot name> entered the game" is evidence that a bot joined.
    """
    wanted = {f"{name} entered the game": name for name in bot_names}
    found: set[str] = set()
    for line in lines:
        plain = strip_color_codes(line).removeprefix("[stderr] ").strip()
        if plain in wanted:
            found.add(wanted[plain])
    return found


def classify_engine_log(lines: list[str]) -> dict[str, list[str]]:
    """Group the engine's own output into the defect classes WP4 gates on.

    A line that matches a reasoned acceptance is recorded under
    ``accepted-note`` together with its reason and is not counted as a defect;
    every other match is.
    """
    found: dict[str, list[str]] = {
        name: [] for name, _source, _pattern in ENGINE_DEFECT_PATTERNS
    }
    found["accepted-note"] = []
    for line in lines:
        accepted = next(
            (
                reason
                for pattern, reason in ACCEPTED_ENGINE_NOTES
                if pattern.search(line)
            ),
            None,
        )
        if accepted is not None:
            found["accepted-note"].append(f"{line.strip()}  [accepted: {accepted}]")
            continue
        for name, _source, pattern in ENGINE_DEFECT_PATTERNS:
            if pattern.search(line):
                found[name].append(line)
    return found


# --------------------------------------------------------------------------
# One run.
# --------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class RunResult:
    index: int
    directory: Path
    snapshot: dict[str, Any] = field(default_factory=dict)
    engine_defects: dict[str, list[str]] = field(default_factory=dict)
    browser_console: list[dict[str, Any]] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    engine_log: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    access_log: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[dict[str, Any]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def _evaluate(
    session: DevToolsSession,
    expression: str,
    *,
    timeout: float = 60.0,
    await_promise: bool = False,
) -> Any:
    result = session.call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        },
        timeout=timeout,
    )
    if result.get("exceptionDetails"):
        raise AcceptanceError(f"page evaluation failed: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


def _snapshot(session: DevToolsSession) -> dict[str, Any]:
    return json.loads(_evaluate(session, "JSON.stringify(window.arenaWeb.snapshot())"))


def _dispatch_click(session: DevToolsSession, x: float, y: float) -> None:
    for event in ("mouseMoved", "mousePressed", "mouseReleased"):
        parameters: dict[str, Any] = {
            "type": event,
            "x": x,
            "y": y,
            "button": "none" if event == "mouseMoved" else "left",
            "buttons": 0 if event != "mousePressed" else 1,
            "clickCount": 0 if event == "mouseMoved" else 1,
        }
        session.call("Input.dispatchMouseEvent", parameters)


def _dispatch_key(
    session: DevToolsSession, code: str, key: str, virtual: int, *, down: bool
) -> None:
    session.call(
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown" if down else "keyUp",
            "code": code,
            "key": key,
            "windowsVirtualKeyCode": virtual,
            "nativeVirtualKeyCode": virtual,
            "text": key if down else "",
        },
    )


def _capture(
    session: DevToolsSession, path: Path, width: int, height: int
) -> dict[str, Any]:
    full = session.call(
        "Page.captureScreenshot",
        {"format": "png", "captureBeyondViewport": False},
        timeout=120,
    )
    data = base64.b64decode(full["data"])
    path.write_bytes(data)
    # A quarter-scale copy is cheap to decode; the saved full-size image stays
    # untouched for a human to look at.
    try:
        small = session.call(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": False,
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": width,
                    "height": height,
                    "scale": 0.25,
                },
            },
            timeout=120,
        )
        statistics = png_pixel_statistics(base64.b64decode(small["data"]))
        statistics["source"] = "quarter-scale clip"
    except (BrowserSessionError, AcceptanceError):
        statistics = png_pixel_statistics(data, sample_stride=4)
        statistics["source"] = "full frame, every fourth column"
    statistics["file"] = path.name
    statistics["fullBytes"] = len(data)
    return statistics


def _collect_events(session: DevToolsSession, result: RunResult) -> None:
    for message in session.drain_events():
        method = message.get("method")
        params = message.get("params", {})
        if method == "Runtime.exceptionThrown":
            result.exceptions.append(params.get("exceptionDetails", {}))
        elif method == "Runtime.consoleAPICalled":
            result.browser_console.append(
                {
                    "source": "console",
                    "level": params.get("type"),
                    "text": " ".join(
                        str(argument.get("value", argument.get("description", "")))
                        for argument in params.get("args", [])
                    ),
                }
            )
        elif method == "Log.entryAdded":
            entry = params.get("entry", {})
            result.browser_console.append(
                {
                    "source": entry.get("source"),
                    "level": entry.get("level"),
                    "text": entry.get("text", ""),
                    "url": entry.get("url"),
                }
            )
        elif method == "Network.requestWillBeSent":
            result.requests.append(params.get("request", {}).get("url", ""))
        elif method == "Network.loadingFailed":
            result.browser_console.append(
                {
                    "source": "network",
                    "level": "error",
                    "text": f"loading failed: {params.get('errorText')}",
                }
            )


def probe_rotation_refusals(
    *,
    chrome: Path,
    serve: StaticServe,
    directory: Path,
    start_map: str,
    boot_timeout: float,
    headless: bool,
    angle_backend: str,
    window: tuple[int, int],
) -> list[Check]:
    """The refusals of the shipped loader, in the pinned browser.

    The run above proves the selection selects. These prove it *refuses*, which
    is the half a passing run cannot show: the rotation is a required parameter
    precisely because both silent defaults are wrong — fetching everything is
    the problem this exists to solve, and fetching one map produces a client
    whose archive set is a strict subset of the server's rotation, which the
    engine drops mid-match with no earlier symptom.

    Every case is loaded into the real page rather than reasoned about, because
    a message the code can produce and a message anyone has seen it produce are
    different claims.

    **One refusal is gone rather than replaced.** WP-D had to refuse Start when
    the rotation did not contain the map the profile committed, because those
    were two independent inputs; WP-E made the started map *be* the rotation's
    first entry, so the archive it needs is in the fetch set by construction
    and no case can produce that refusal. It is not re-created here with a
    synthetic one: what is left is the five refusals that still have subject
    matter, plus the positive check that the offline slice starts the entry the
    caller put first.
    """
    cases = [
        ("no-rotation", "", "must be opened with ?maps="),
        ("empty-rotation", "?maps=", "is empty"),
        ("empty-entry", f"?maps={start_map},", "is empty"),
        ("unknown-map", "?maps=no_such_map", "publishes no archive for no_such_map"),
        # The base is implicit and unnameable: its archive carries no map, so a
        # rotation cannot ask for it and cannot leave it out either.
        ("base-is-not-selectable", "?maps=arena-web-ffa-base", "publishes no archive"),
    ]
    checks: list[Check] = []
    browser = ChromeProcess(
        chrome,
        directory / "profile",
        headless=headless,
        window_size=window,
        angle_backend=angle_backend,
    )
    browser.start()
    try:
        session = browser.page_session()
        for domain in ("Page", "Runtime"):
            session.call(f"{domain}.enable")
        for name, query, needle in cases:
            session.call("Page.navigate", {"url": f"{serve.origin}/{query}"})
            wait_until(
                lambda: _evaluate(session, "window.arenaWeb?.report?.status")
                in ("ready", "failed"),
                timeout=boot_timeout,
                description=f"the loader settling for {name}",
            )
            status = _evaluate(session, "window.arenaWeb.report.status")
            message = _evaluate(
                session, "window.arenaWeb.report.error?.message ?? ''"
            )
            checks.append(
                Check(
                    f"rotation-refused-{name}",
                    status == "failed" and needle in message,
                    f"status '{status}', message '{message}'",
                )
            )
        # The sixth case, and the one that keeps the loader's click handler from
        # becoming a comment about something that cannot happen. After the two
        # rotation refusals WP-E deleted, exactly one `start()` rejection is
        # still reachable while the page is `ready` and nothing has been
        # consumed: a click that carries no transient user activation. A real
        # click always carries one, so the only way in is a synthetic
        # `element.click()` — which a host page or an embedder can produce, and
        # which the five cases above never reach because they fail before
        # `ready` and never call `start()` at all.
        session.call("Page.navigate", {"url": f"{serve.origin}/?maps={start_map}"})
        wait_until(
            lambda: _evaluate(session, "window.arenaWeb?.report?.status") == "ready",
            timeout=boot_timeout,
            description="the loader becoming ready for the synthetic-click case",
        )
        _evaluate(session, "document.getElementById('start').click()")
        wait_until(
            lambda: "Cannot start"
            in (_evaluate(session, "document.getElementById('message').textContent") or ""),
            timeout=15.0,
            description="the refusal reaching the overlay",
        )
        overlay = _evaluate(session, "document.getElementById('message').textContent")
        checks.append(
            Check(
                "start-refusal-is-visible",
                isinstance(overlay, str)
                and "Cannot start" in overlay
                and "transient user activation" in overlay,
                f"overlay said '{overlay}'",
            )
        )
    finally:
        browser.stop()
    return checks


def run_once(
    *,
    index: int,
    chrome: Path,
    serve: StaticServe,
    output_root: Path,
    expected: Expectations,
    window: tuple[int, int],
    play_seconds: float,
    boot_timeout: float,
    headless: bool,
    angle_backend: str,
) -> RunResult:
    directory = output_root / f"run-{index}"
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    result = RunResult(index=index, directory=directory)

    access_start = len(serve.access_log)
    browser = ChromeProcess(
        chrome,
        directory / "profile",
        headless=headless,
        window_size=window,
        angle_backend=angle_backend,
    )
    browser.start()
    session: DevToolsSession | None = None
    try:
        session = browser.page_session()
        for domain in ("Page", "Runtime", "Log", "Network"):
            session.call(f"{domain}.enable")
        session.call(
            "Page.navigate",
            {"url": f"{serve.origin}/?maps={expected.rotation_parameter}"},
        )

        def poll(expression: str) -> Any:
            _collect_events(session, result)
            return _evaluate(session, expression)

        # The loader verifies every declared artifact against the committed
        # manifests before it offers to start, so this wait covers fetching and
        # hashing all of it.
        wait_until(
            lambda: poll("window.arenaWeb?.report?.status") in ("ready", "failed"),
            timeout=boot_timeout,
            description="the loader becoming ready",
        )
        status = _evaluate(session, "window.arenaWeb.report.status")
        result.checks.append(
            Check("loader-ready", status == "ready", f"status was '{status}'")
        )
        if status != "ready":
            raise AcceptanceError(f"the loader stopped at status '{status}'")

        rectangle = json.loads(
            _evaluate(
                session,
                "JSON.stringify(document.getElementById('start').getBoundingClientRect())",
            )
        )
        # A trusted input event, so the page really gains user activation and the
        # audio evidence is not produced by a synthetic click.
        _dispatch_click(
            session,
            rectangle["x"] + rectangle["width"] / 2,
            rectangle["y"] + rectangle["height"] / 2,
        )

        wait_until(
            lambda: poll("window.arenaWeb.report.markers.clientGameLoaded") is not None,
            timeout=boot_timeout,
            description="the client game module entering the map",
        )
        result.screenshots.append(
            _capture(session, directory / "01-map-entered.png", *window)
        )

        # Bots join on ioquake3's own 2000/3500/5000 ms addbot cadence, and each
        # one is waited for by name. A miss is recorded by the bots-entered-game
        # check rather than aborting the run, so the evidence still shows
        # everything else.
        try:
            wait_until(
                lambda: poll("window.arenaWeb.report.botEntries.length")
                >= len(expected.bot_names),
                timeout=60,
                description=f"all {len(expected.bot_names)} bots entering the game",
            )
        except TimeoutError:
            pass

        # The loader's ResizeObserver closes the browser-only gap where a CSS
        # or element-fullscreen change resizes the canvas without a native
        # window resize. Device emulation gives this automated round two exact
        # viewport sizes; the headed witnessed round still owns real
        # compositor fullscreen behaviour.
        initial_render = _snapshot(session).get("render") or {}
        initial_resize_events = int(initial_render.get("resizeEvents") or 0)
        initial_width = int(initial_render.get("cssWidth") or window[0])
        initial_height = int(initial_render.get("cssHeight") or window[1])
        target_width = initial_width - 160 if initial_width > 480 else initial_width + 160
        target_height = initial_height - 90 if initial_height > 330 else initial_height + 90
        target_mode = f"MODE: -1, {target_width} x {target_height}"
        initial_mode = (
            f"MODE: -1, {initial_render.get('cssWidth')} x {initial_render.get('cssHeight')}"
        )
        initial_mode_count = int(
            _evaluate(
                session,
                "window.arenaWeb.engineLog().filter((line) => "
                f"line.includes({json.dumps(initial_mode)})).length",
            )
            or 0
        )
        resize_observed = False
        resize_adopted = False
        restored = False
        restore_adopted = False
        try:
            session.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": target_width,
                    "height": target_height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            wait_until(
                lambda: (
                    (current := _snapshot(session).get("render") or {}).get("cssWidth")
                    == target_width
                    and current.get("cssHeight") == target_height
                    and int(current.get("resizeEvents") or 0) > initial_resize_events
                ),
                timeout=20,
                description="the loader observing the resized canvas",
            )
            resize_observed = True
            wait_until(
                lambda: _evaluate(
                    session,
                    f"window.arenaWeb.engineLog().some((line) => line.includes({json.dumps(target_mode)}))",
                ),
                timeout=20,
                description="the engine adopting the resized canvas",
            )
            resize_adopted = True
        except TimeoutError:
            pass
        finally:
            session.call("Emulation.clearDeviceMetricsOverride")

        try:
            wait_until(
                lambda: (
                    (current := _snapshot(session).get("render") or {}).get("cssWidth")
                    == initial_render.get("cssWidth")
                    and current.get("cssHeight") == initial_render.get("cssHeight")
                ),
                timeout=20,
                description="the canvas returning to its initial size",
            )
            restored = True
            wait_until(
                lambda: int(
                    _evaluate(
                        session,
                        "window.arenaWeb.engineLog().filter((line) => "
                        f"line.includes({json.dumps(initial_mode)})).length",
                    )
                    or 0
                )
                > initial_mode_count,
                timeout=20,
                description="the engine returning to the initial canvas size",
            )
            restore_adopted = True
        except TimeoutError:
            pass
        result.checks.extend(
            [
                Check(
                    "loader-observed-runtime-resize",
                    resize_observed,
                    f"initial {initial_render.get('cssWidth')}x{initial_render.get('cssHeight')}, "
                    f"target {target_width}x{target_height}",
                ),
                Check(
                    "engine-adopted-runtime-resize",
                    resize_adopted,
                    f"expected engine marker '{target_mode}'",
                ),
                Check(
                    "canvas-returned-to-initial-size",
                    restored and restore_adopted,
                    f"expected {initial_render.get('cssWidth')}x{initial_render.get('cssHeight')}",
                ),
            ]
        )

        # Input the browser treats as real. Nothing here claims that the player
        # moved: it claims that trusted key and mouse events reach a running
        # client without breaking it.
        deadline = time.monotonic() + play_seconds
        centre_x, centre_y = window[0] / 2, window[1] / 2
        _dispatch_click(session, centre_x, centre_y)
        step = 0
        while time.monotonic() < deadline:
            code, key, virtual = MOVEMENT_KEYS[step % len(MOVEMENT_KEYS)]
            _dispatch_key(session, code, key, virtual, down=True)
            for offset in (-60, 60):
                session.call(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseMoved",
                        "x": centre_x + offset,
                        "y": centre_y,
                        "button": "none",
                        "buttons": 0,
                    },
                )
                time.sleep(0.2)
            _dispatch_key(session, code, key, virtual, down=False)
            if step % 2 == 0:
                _dispatch_click(session, centre_x, centre_y)
            step += 1
            _collect_events(session, result)
        result.screenshots.append(
            _capture(session, directory / "02-after-input.png", *window)
        )

        # Focus loss and recovery. These are dispatched events, not a window
        # manager switching windows, so they exercise the engine's own focus
        # path (SDL's blur/focus callbacks) and nothing more.
        _evaluate(session, "window.dispatchEvent(new Event('blur'))")
        time.sleep(1.5)
        _evaluate(session, "window.dispatchEvent(new Event('focus'))")
        time.sleep(1.5)
        result.screenshots.append(
            _capture(session, directory / "03-after-focus.png", *window)
        )

        result.checks.append(Check("run-completed", True))
    except (BrowserSessionError, TimeoutError, AcceptanceError) as error:
        result.checks.append(Check("run-completed", False, str(error)))
    finally:
        # Evidence is collected even when the run failed: a run that never
        # reached the map is exactly the one whose engine console matters.
        if session is not None:
            try:
                _collect_events(session, result)
                result.snapshot = _snapshot(session)
                engine_log = json.loads(
                    _evaluate(session, "JSON.stringify(window.arenaWeb.engineLog())")
                )
                (directory / "engine-console.log").write_text(
                    "\n".join(engine_log) + "\n", encoding="utf-8"
                )
                result.engine_log = engine_log
                result.engine_defects = classify_engine_log(engine_log)
            except (
                BrowserSessionError,
                TimeoutError,
                AcceptanceError,
                OSError,
            ) as error:
                result.checks.append(
                    Check(
                        "evidence-collected", False, f"{type(error).__name__}: {error}"
                    )
                )
            session.close()
        browser.stop()

    result.access_log = serve.access_log[access_start:]
    _score(result, expected)
    (result.directory / "result.json").write_text(
        json.dumps(
            {
                "index": result.index,
                "checks": [check.__dict__ for check in result.checks],
                "snapshot": result.snapshot,
                "engineDefects": result.engine_defects,
                "browserConsole": result.browser_console,
                "exceptions": result.exceptions,
                "requests": sorted(set(result.requests)),
                "accessLog": result.access_log,
                "screenshots": result.screenshots,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def _score(result: RunResult, expected: Expectations) -> None:
    snapshot = result.snapshot
    if not snapshot:
        result.checks.append(
            Check("snapshot-collected", False, "the page produced no report")
        )
        return
    result.checks.append(Check("snapshot-collected", True))

    # The browser's own verdict is not enough: the digests it reported are
    # compared here against the committed manifests read from this checkout, so
    # the check does not depend on what the page was served or believed.
    identities = snapshot.get("identities", [])
    mismatched = [item["served"] for item in identities if not item.get("matches")]
    reported = {item["served"]: item.get("actualSha256") for item in identities}
    # Against the *selected* artifacts, which is what the page was asked to
    # fetch. Comparing against every committed artifact would fail any run with
    # a rotation, and comparing against only what the page reported would let a
    # run that skipped an archive pass. So the expected set is the committed one
    # minus exactly the archives this rotation excludes.
    selected_digests = {
        served: digest
        for served, digest in expected.artifact_digests.items()
        if served not in expected.rotation_excluded
    }
    result.checks.append(
        Check(
            "runtime-identities-match-committed-manifests",
            reported == selected_digests and not mismatched,
            f"{len(identities)} artifacts against {len(selected_digests)} selected "
            f"of {len(expected.artifact_digests)} committed identities, "
            f"mismatched: {mismatched}",
        )
    )

    configured = {
        entry["served"]: entry["sha256"] for entry in snapshot.get("configFiles", [])
    }
    result.checks.append(
        Check(
            "engine-configuration-is-the-repository-file",
            bool(configured) and configured == expected.config_digests,
            f"{sorted(configured)} against {sorted(expected.config_digests)}",
        )
    )

    markers = snapshot.get("markers", {})
    result.checks.append(
        Check(
            "engine-started",
            snapshot.get("timings", {}).get("runtimeInitializedMs") is not None,
        )
    )
    result.checks.append(
        Check(
            "map-entered",
            markers.get("serverSpawned") is not None
            and markers.get("clientGameLoaded") is not None,
            f"markers: {sorted(markers)}",
        )
    )
    # F1: "entered the game" is printed for every client and the local player is
    # always first (ioq3 code/game/g_client.c:1026), so the generic marker
    # cannot be the bot gate. Every configured bot has to be named, and the two
    # independent derivations — the loader's, taken live off the print stream,
    # and this one, recomputed here from the saved log — have to agree.
    reported_bots = {
        entry["name"] for entry in snapshot.get("botEntries", []) if entry.get("name")
    }
    log_bots = bots_from_engine_log(result.engine_log, expected.bot_names)
    missing_bots = sorted(set(expected.bot_names) - reported_bots)
    result.checks.append(
        Check(
            "bots-entered-game",
            bool(expected.bot_names) and not missing_bots and reported_bots == log_bots,
            f"configured {sorted(expected.bot_names)}, page reported "
            f"{sorted(reported_bots)}, engine log shows {sorted(log_bots)}",
        )
    )

    # F3: ERR_FATAL never carries the 'ERROR: ' prefix the pattern above looks
    # for, so a fatal stop is caught by the page's own final state instead.
    exit_events = [
        event
        for event in snapshot.get("events", [])
        if event.get("kind") == "engine-exit"
    ]
    result.checks.append(
        Check(
            "engine-kept-running",
            snapshot.get("status") == "running"
            and snapshot.get("error") is None
            and not exit_events,
            f"status {snapshot.get('status')!r}, error {snapshot.get('error')}, "
            f"exit events {exit_events}",
        )
    )

    # F5: the arguments the engine actually received are the rotation's map,
    # then the committed ones, then exactly the render-size suffix the loader
    # derives from its canvas.
    render = snapshot.get("render") or {}
    startup_width = render.get("startupCssWidth", render.get("cssWidth"))
    startup_height = render.get("startupCssHeight", render.get("cssHeight"))
    expected_arguments = list(expected.engine_arguments) + [
        "+set",
        "r_mode",
        "-1",
        "+set",
        "r_customwidth",
        str(startup_width),
        "+set",
        "r_customheight",
        str(startup_height),
    ]
    result.checks.append(
        Check(
            "engine-arguments-are-the-rotation-then-the-committed-profile",
            snapshot.get("engineArguments") == expected_arguments,
            " ".join(snapshot.get("engineArguments") or []),
        )
    )

    for name, _source, _pattern in ENGINE_DEFECT_PATTERNS:
        lines = result.engine_defects.get(name, [])
        result.checks.append(
            Check(
                f"engine-console-no-{name}",
                not lines,
                f"{len(lines)} line(s): {lines[:3]}",
            )
        )

    errors = [
        entry
        for entry in result.browser_console
        if entry.get("level") in ("error", "assert")
    ]
    result.checks.append(
        Check(
            "browser-console-no-error",
            not errors,
            f"{len(errors)} entries: {errors[:3]}",
        )
    )
    result.checks.append(
        Check(
            "no-uncaught-exception",
            not result.exceptions and not snapshot.get("browserErrors"),
            f"{len(result.exceptions)} exception(s), {len(snapshot.get('browserErrors', []))} page error(s)",
        )
    )

    # F4: a foreign origin whose path happens to match a staged name must not
    # pass, and the bare origin root has to be recognised as the loader page
    # rather than skipped.
    unexpected_urls = []
    for url in set(result.requests):
        if url.startswith(BROWSER_ALLOWED_SCHEMES):
            continue
        if not url.startswith(f"{expected.origin}/"):
            unexpected_urls.append(url)
            continue
        path = urlsplit(url).path.lstrip("/")
        if path in ("", "index.html") or path in expected.files:
            continue
        unexpected_urls.append(url)
    result.checks.append(
        Check(
            "only-declared-local-artifacts",
            not unexpected_urls,
            f"unexpected: {unexpected_urls}",
        )
    )

    served_paths = {
        entry["path"].lstrip("/").split("?", 1)[0] for entry in result.access_log
    }
    non_ok = [entry for entry in result.access_log if entry["status"] != 200]
    undeclared = sorted(
        path for path in served_paths if path and path not in expected.files
    )
    result.checks.append(
        Check(
            "serve-answered-only-staged-files",
            not undeclared and not non_ok,
            f"undeclared: {undeclared}, non-200: {non_ok}",
        )
    )
    result.checks.append(
        Check(
            "no-unexpected-engine-file-request",
            not snapshot.get("unexpectedFileRequests"),
            str(snapshot.get("unexpectedFileRequests")),
        )
    )

    # The fetch selection, in a real browser, both ways. The page is opened with
    # a deliberately unsorted parameter carrying a repeated name, because a
    # caller passes its rotation *list* and a rotation may play a map twice per
    # cycle: two spellings of one set must fetch one set.
    rotation = snapshot.get("rotation") or {}
    result.checks.append(
        Check(
            "rotation-canonicalised",
            tuple(rotation.get("resolved") or ()) == expected.rotation,
            f"resolved {rotation.get('resolved')} from '{rotation.get('parameter')}'",
        )
    )
    # The ordering claim, live. The parameter's *first* entry is where the
    # rotation starts, and it is what the offline slice must have come up on —
    # proven from the engine's own `Server: <map>` line through the templated
    # marker, not from the page's account of itself.
    result.checks.append(
        Check(
            "offline-starts-the-rotations-first-entry",
            rotation.get("startMap") == expected.start_map
            and snapshot.get("markers", {}).get("serverSpawned") is not None
            and any(
                line.strip().endswith(f"Server: {expected.start_map}")
                for line in result.engine_log
            )
            # And no *other* spawn line, which is what a prefix pair could
            # otherwise hide: `Server: am_galmevish2` ends with neither the
            # needle for `am_galmevish` nor this one.
            and not any(
                line.strip().startswith("Server: ")
                and not line.strip().endswith(f"Server: {expected.start_map}")
                for line in result.engine_log
            ),
            f"startMap {rotation.get('startMap')}, expected {expected.start_map}",
        )
    )
    fetched_archives = served_paths & (expected.rotation_served | expected.rotation_excluded)
    result.checks.append(
        Check(
            "rotation-fetched-exactly-its-archives",
            fetched_archives == set(expected.rotation_served),
            f"missing {sorted(set(expected.rotation_served) - fetched_archives)}, "
            f"unwanted {sorted(fetched_archives & expected.rotation_excluded)}",
        )
    )

    frames = snapshot.get("frames", {})
    result.checks.append(
        Check(
            "frames-advanced",
            frames.get("samples", 0) > 100 and frames.get("meanFps", 0) > 5,
            f"{frames.get('samples')} samples, mean {frames.get('meanFps')} fps",
        )
    )

    audio = snapshot.get("audioActivation") or {}
    result.checks.append(
        Check(
            "audio-user-activated",
            audio.get("state") == "running" and bool(audio.get("userActivation")),
            json.dumps(audio),
        )
    )

    rendered = [shot for shot in result.screenshots if shot["distinctColours"] > 64]
    result.checks.append(
        Check(
            "canvas-rendered-a-scene",
            len(rendered) == len(result.screenshots) and bool(result.screenshots),
            ", ".join(
                f"{shot['file']}: {shot['distinctColours']} colours"
                for shot in result.screenshots
            ),
        )
    )

    # The renderer defect the first witnessed round found (wp4-vertical-slice.md):
    # under Emscripten/WebGL the lightmapped world-surface path intermittently
    # painted surfaces solid white — 15 to 52 percent of the frame in a defective
    # session against well under one percent in a healthy one, in roughly two of
    # three sessions. The map-entered shot is excluded because the loading screen
    # may legitimately be bright; the in-game shots are the evidence.
    in_game = [
        shot for shot in result.screenshots if shot["file"] != "01-map-entered.png"
    ]
    result.checks.append(
        Check(
            "canvas-no-white-surface-regression",
            bool(in_game)
            and all(shot["nearWhiteFraction"] < 0.05 for shot in in_game),
            ", ".join(
                f"{shot['file']}: {shot['nearWhiteFraction']:.2%} near-white"
                for shot in in_game
            ),
        )
    )


def compare_runs(first: RunResult, second: RunResult) -> list[Check]:
    """The second clean launch must reach the same profile from the same bytes."""
    checks: list[Check] = []
    first_identities = {
        item["served"]: item["actualSha256"]
        for item in first.snapshot.get("identities", [])
    }
    second_identities = {
        item["served"]: item["actualSha256"]
        for item in second.snapshot.get("identities", [])
    }
    checks.append(
        Check(
            "second-launch-same-artifact-identities",
            bool(first_identities) and first_identities == second_identities,
            f"{len(second_identities)} artifacts",
        )
    )
    checks.append(
        Check(
            "second-launch-same-engine-arguments",
            first.snapshot.get("engineArguments")
            == second.snapshot.get("engineArguments"),
            " ".join(second.snapshot.get("engineArguments", [])),
        )
    )
    checks.append(
        Check(
            "second-launch-reached-the-same-profile",
            second.snapshot.get("markers", {}).get("clientGameLoaded") is not None
            and second.snapshot.get("profile") == first.snapshot.get("profile"),
            json.dumps(second.snapshot.get("profile")),
        )
    )
    return checks


def run_acceptance(
    *,
    chrome: Path,
    engine_dir: Path,
    content_dir: Path,
    serve_dir: Path,
    output_root: Path,
    runs: int = 2,
    play_seconds: float = 25.0,
    boot_timeout: float = 300.0,
    window: tuple[int, int] = (1280, 720),
    headless: bool = True,
    angle_backend: str = "gl",
    skip_stage: bool = False,
    rotation: str | None = None,
) -> dict[str, Any]:
    if skip_stage:
        verify_staged(REPO_ROOT, serve_dir)
    else:
        stage(REPO_ROOT, serve_dir, engine_dir=engine_dir, content_dir=content_dir)
    profile = load_profile(REPO_ROOT)
    files = served_files(REPO_ROOT, profile)
    expected_files = frozenset(files)
    expected_config_digests = {
        served: file_sha256(entry["source"])
        for served, entry in files.items()
        if entry["kind"] == "config"
    }
    expected_artifact_digests = {
        served: entry["sha256"]
        for served, entry in files.items()
        if entry["kind"] == "artifact"
    }

    # The rotation this acceptance opens the page for. It is derived rather
    # than written down, so publishing a map needs no edit here, and it is a
    # strict subset of the published set on purpose: the point of the run is
    # that the archives outside it are never fetched.
    #
    # `--rotation` overrides it, and that is what makes a per-map browser sweep
    # possible at all: the offline slice starts the rotation's own first entry,
    # so pointing this at one published map renders that map in the pinned
    # browser. Before WP-E the started map was committed and no parameter could
    # move it, which is why the published archives had no automated rendering
    # evidence beyond the one map the profile named.
    content_manifest = json.loads(
        (REPO_ROOT / profile["manifests"]["content"]).read_text(encoding="utf-8")
    )
    archive_by_map = {
        artifact["map"]: artifact["path"]
        for artifact in content_manifest["artifacts"]
        if isinstance(artifact.get("map"), str)
    }
    published = sorted(archive_by_map)
    if len(published) < 2:
        raise AcceptanceError(
            "the release publishes one map archive, so a selection cannot be "
            "distinguished from fetching everything"
        )
    if rotation is None:
        # Unsorted, and with a repeat, so the canonicalisation is exercised in
        # the browser rather than only in a unit test. The second entry is the
        # start map, which is the ordering claim this run then checks live.
        rotation_parameter = ",".join((published[1], published[0], published[1]))
    else:
        rotation_parameter = rotation
    requested = [name.strip() for name in rotation_parameter.split(",")]
    unknown = sorted(set(requested) - set(archive_by_map))
    if unknown:
        raise AcceptanceError(f"the release publishes no archive for {unknown}")
    start_map = requested[0]
    rotation = tuple(sorted(set(requested)))
    served_by_path = {
        entry["artifactPath"]: served
        for served, entry in files.items()
        if entry["kind"] == "artifact" and entry.get("manifest") == "content"
    }
    rotation_served = frozenset(
        served
        for path, served in served_by_path.items()
        if path not in archive_by_map.values()
        or path in {archive_by_map[name] for name in rotation}
    )
    rotation_excluded = frozenset(served_by_path.values()) - rotation_served
    if not rotation_excluded:
        raise AcceptanceError("the rotation covers every archive; nothing is excluded")

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    # The acceptance browser is a pin, not a preference: a different build is
    # refused before a single page is loaded.
    expected_version = pinned_browser_version(REPO_ROOT)
    version = ChromeProcess(chrome, output_root / "browser-version-probe").version()
    if f"Chrome for Testing {expected_version}" not in version:
        raise AcceptanceError(
            f"{chrome} reports '{version}', not the pinned "
            f"'Google Chrome for Testing {expected_version}'"
        )

    results: list[RunResult] = []
    with StaticServe(serve_dir) as serve:
        expectations = Expectations(
            files=expected_files,
            origin=serve.origin,
            config_digests=expected_config_digests,
            artifact_digests=expected_artifact_digests,
            # What the page must actually hand the engine: the offline map,
            # prepended, and then the committed list. The map is no longer in
            # the committed half, so an acceptance that compared against that
            # half alone would no longer be checking the argument the rotation
            # decides.
            engine_arguments=tuple(
                offline_map_arguments([start_map]) + profile["engineArguments"]
            ),
            start_map=start_map,
            bot_names=tuple(bot["name"] for bot in profile["bots"]),
            rotation=rotation,
            rotation_parameter=rotation_parameter,
            rotation_served=rotation_served,
            rotation_excluded=rotation_excluded,
        )
        for index in range(1, runs + 1):
            results.append(
                run_once(
                    index=index,
                    chrome=chrome,
                    serve=serve,
                    output_root=output_root,
                    expected=expectations,
                    window=window,
                    play_seconds=play_seconds,
                    boot_timeout=boot_timeout,
                    headless=headless,
                    angle_backend=angle_backend,
                )
            )

        refusals = probe_rotation_refusals(
            chrome=chrome,
            serve=serve,
            directory=output_root / "rotation-refusals",
            start_map=start_map,
            boot_timeout=boot_timeout,
            headless=headless,
            angle_backend=angle_backend,
            window=window,
        )

    comparison = compare_runs(results[0], results[-1]) if len(results) > 1 else []
    summary = {
        "browser": version,
        "headless": headless,
        "angleBackend": angle_backend if headless else "browser default",
        "window": list(window),
        "servedFiles": sorted(expected_files),
        "runs": [
            {
                "index": result.index,
                "passed": result.passed,
                "checks": [check.__dict__ for check in result.checks],
                "timings": result.snapshot.get("timings", {}),
                "frames": result.snapshot.get("frames", {}),
                "markers": result.snapshot.get("markers", {}),
                "botEntries": result.snapshot.get("botEntries", []),
                "totalArtifactBytes": result.snapshot.get("totalArtifactBytes"),
                "render": result.snapshot.get("render"),
                "screenshots": result.screenshots,
                "acceptedEngineNotes": result.engine_defects.get("accepted-note", []),
            }
            for result in results
        ],
        "comparison": [check.__dict__ for check in comparison],
        "rotationRefusals": [check.__dict__ for check in refusals],
        "passed": all(result.passed for result in results)
        and all(check.passed for check in comparison)
        and all(check.passed for check in refusals),
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chrome",
        type=Path,
        required=True,
        help="the pinned Chrome for Testing binary",
    )
    parser.add_argument(
        "--engine-dir", type=Path, default=REPO_ROOT / "build/browser/tree/Release"
    )
    parser.add_argument(
        "--content-dir", type=Path, default=REPO_ROOT / "build/content-pack"
    )
    parser.add_argument(
        "--serve-dir", type=Path, default=REPO_ROOT / "build/arena-serve"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / "build/arena-acceptance"
    )
    parser.add_argument("--runs", type=int, default=2)
    # The rotation to open the page with. Without it the acceptance derives a
    # two-map one from the published set; with it, one published map per run is
    # what makes a per-map browser sweep possible.
    parser.add_argument("--rotation", default=None)
    parser.add_argument("--play-seconds", type=float, default=25.0)
    parser.add_argument("--boot-timeout", type=float, default=300.0)
    parser.add_argument(
        "--headed", action="store_true", help="run with a visible window"
    )
    parser.add_argument(
        "--angle",
        default="gl",
        help="headless ANGLE backend: gl, vulkan or swiftshader (default: gl)",
    )
    parser.add_argument(
        "--skip-stage", action="store_true", help="use an already staged serve tree"
    )
    arguments = parser.parse_args(argv)

    try:
        summary = run_acceptance(
            chrome=arguments.chrome.resolve(),
            engine_dir=arguments.engine_dir.resolve(),
            content_dir=arguments.content_dir.resolve(),
            serve_dir=arguments.serve_dir.resolve(),
            output_root=arguments.output_dir.resolve(),
            runs=arguments.runs,
            play_seconds=arguments.play_seconds,
            boot_timeout=arguments.boot_timeout,
            headless=not arguments.headed,
            angle_backend=arguments.angle,
            skip_stage=arguments.skip_stage,
            rotation=arguments.rotation,
        )
    except (ArenaRuntimeError, AcceptanceError, BrowserSessionError) as error:
        print(f"pre-acceptance could not run: {error}", file=sys.stderr)
        return 2

    print(f"browser: {summary['browser']} ({summary['angleBackend']})")
    for run in summary["runs"]:
        print(f"run {run['index']}: {'PASS' if run['passed'] else 'FAIL'}")
        for check in run["checks"]:
            mark = "ok  " if check["passed"] else "FAIL"
            print(f"  [{mark}] {check['name']} {check['detail']}")
    for check in summary["comparison"]:
        mark = "ok  " if check["passed"] else "FAIL"
        print(f"  [{mark}] {check['name']} {check['detail']}")
    print("rotation refusals:")
    for check in summary["rotationRefusals"]:
        mark = "ok  " if check["passed"] else "FAIL"
        print(f"  [{mark}] {check['name']} {check['detail']}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
