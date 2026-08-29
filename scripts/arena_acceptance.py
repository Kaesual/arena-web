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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena_runtime import (  # noqa: E402
    ArenaRuntimeError,
    file_sha256,
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
        " client/snd_dma.c S_FindName; client/snd_openal.c S_AL_BufferUseDefault",
        re.compile(
            r"R_FindImageFile could not find"
            r"|could not find .* - using default"
            r"|Failed to (?:load|open) sound"
            r"|Using default sound for",
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
        "qcommon/common.c Com_Error",
        re.compile(r"^\s*ERROR: "),
    ),
)

# References the engine reports missing that this profile does not need, each
# with the reason it is acceptable. They are recorded in the evidence, never
# silently dropped, and anything the engine reports missing that is *not* on
# this list fails the run. The list is deliberately literal: a wildcard here
# would hide the next real gap.
ACCEPTED_ENGINE_NOTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"Failed to (?:load|open) sound music/sonic5\.wav"),
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
            counted += 1
        previous = line

    return {
        "width": width,
        "height": height,
        "sampledPixels": counted,
        "distinctColours": len(colours),
        "meanLuminance": round(luminance_total / counted, 2) if counted else 0.0,
        "bytes": len(data),
    }


# --------------------------------------------------------------------------
# Engine log classification.
# --------------------------------------------------------------------------


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
    requests: list[str] = field(default_factory=list)
    access_log: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[dict[str, Any]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def _evaluate(
    session: DevToolsSession, expression: str, *, timeout: float = 60.0
) -> Any:
    result = session.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": False},
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


def run_once(
    *,
    index: int,
    chrome: Path,
    serve: StaticServe,
    output_root: Path,
    expected_files: set[str],
    expected_config_digests: dict[str, str],
    expected_artifact_digests: dict[str, str],
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
        session.call("Page.navigate", {"url": f"{serve.origin}/"})

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

        # Bots join on ioquake3's own 2000/3500/5000 ms addbot cadence. A miss
        # is recorded by the bots-entered-game check rather than aborting the
        # run, so the evidence still shows everything else.
        try:
            wait_until(
                lambda: poll("window.arenaWeb.report.markers.botEnteredGame")
                is not None,
                timeout=60,
                description="a bot entering the game",
            )
        except TimeoutError:
            pass

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
    _score(result, expected_files, expected_config_digests, expected_artifact_digests)
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


def _score(
    result: RunResult,
    expected_files: set[str],
    expected_config_digests: dict[str, str],
    expected_artifact_digests: dict[str, str],
) -> None:
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
    result.checks.append(
        Check(
            "runtime-identities-match-committed-manifests",
            reported == expected_artifact_digests and not mismatched,
            f"{len(identities)} artifacts against {len(expected_artifact_digests)} "
            f"committed identities, mismatched: {mismatched}",
        )
    )

    configured = {
        entry["served"]: entry["sha256"] for entry in snapshot.get("configFiles", [])
    }
    result.checks.append(
        Check(
            "engine-configuration-is-the-repository-file",
            bool(configured) and configured == expected_config_digests,
            f"{sorted(configured)} against {sorted(expected_config_digests)}",
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
    result.checks.append(
        Check("bots-entered-game", markers.get("botEnteredGame") is not None)
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

    unexpected_urls = []
    for url in set(result.requests):
        if url.startswith(BROWSER_ALLOWED_SCHEMES):
            continue
        path = url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        if path == "" or path in expected_files:
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
        path for path in served_paths if path and path not in expected_files
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
) -> dict[str, Any]:
    if skip_stage:
        verify_staged(REPO_ROOT, serve_dir)
    else:
        stage(REPO_ROOT, serve_dir, engine_dir=engine_dir, content_dir=content_dir)
    profile = load_profile(REPO_ROOT)
    files = served_files(REPO_ROOT, profile)
    expected_files = set(files)
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
        for index in range(1, runs + 1):
            results.append(
                run_once(
                    index=index,
                    chrome=chrome,
                    serve=serve,
                    output_root=output_root,
                    expected_files=expected_files,
                    expected_config_digests=expected_config_digests,
                    expected_artifact_digests=expected_artifact_digests,
                    window=window,
                    play_seconds=play_seconds,
                    boot_timeout=boot_timeout,
                    headless=headless,
                    angle_backend=angle_backend,
                )
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
                "totalArtifactBytes": result.snapshot.get("totalArtifactBytes"),
                "render": result.snapshot.get("render"),
                "screenshots": result.screenshots,
                "acceptedEngineNotes": result.engine_defects.get("accepted-note", []),
            }
            for result in results
        ],
        "comparison": [check.__dict__ for check in comparison],
        "passed": all(result.passed for result in results)
        and all(check.passed for check in comparison),
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
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
