#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One pinned-browser smoke of the WP11 host lifecycle and real engine exit."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena_acceptance import (  # noqa: E402
    AcceptanceError,
    StaticServe,
    _dispatch_click,
    _evaluate,
    _snapshot,
    pinned_browser_version,
)
from arena_runtime import ArenaRuntimeError, stage, verify_staged  # noqa: E402
from browser_session import (  # noqa: E402
    BrowserSessionError,
    ChromeProcess,
    wait_until,
)

ROOT = Path(__file__).resolve().parent.parent


def rectangle(session, element_id: str) -> dict[str, float]:
    return json.loads(
        _evaluate(
            session,
            "JSON.stringify(document.getElementById("
            + json.dumps(element_id)
            + ").getBoundingClientRect())",
        )
    )


def click_element(session, element_id: str) -> None:
    box = rectangle(session, element_id)
    _dispatch_click(
        session,
        box["x"] + box["width"] / 2,
        box["y"] + box["height"] / 2,
    )


def early_stop_checks(
    chrome: Path,
    server: StaticServe,
    output_dir: Path,
    *,
    during_loading: bool,
    headless: bool,
) -> list[dict]:
    name = "loading" if during_loading else "booting"
    browser = ChromeProcess(
        chrome,
        output_dir / f"{name}-profile",
        headless=headless,
        window_size=(960, 540),
        angle_backend="gl",
    )
    session = None
    try:
        browser.start()
        session = browser.page_session()
        for domain in ("Page", "Runtime", "Network"):
            session.call(f"{domain}.enable")
        if during_loading:
            # Keep the initial same-origin profile/artifact requests in flight
            # long enough to make the stop point deterministic. This is browser
            # network emulation, not a product/runtime input.
            session.call(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "latency": 100,
                    "downloadThroughput": 64 * 1024,
                    "uploadThroughput": 64 * 1024,
                    "connectionType": "wifi",
                },
            )
        session.call("Page.navigate", {"url": f"{server.origin}/"})
        wait_until(
            lambda: bool(_evaluate(session, "window.arenaWeb")),
            timeout=30,
            description="the early host API",
        )
        if during_loading:
            initial = _evaluate(session, "arenaWeb.snapshot().status")
            _evaluate(
                session,
                """
                window.wp11Early = {};
                window.wp11Early.stopA = arenaWeb.stop();
                window.wp11Early.stopB = arenaWeb.stop();
                window.wp11Early.sameStop = window.wp11Early.stopA === window.wp11Early.stopB;
                window.wp11Early.stopA.then((value) => { window.wp11Early.terminal = value; });
                """,
            )
        else:
            wait_until(
                lambda: _evaluate(session, "arenaWeb.snapshot().status")
                in ("ready", "failed"),
                timeout=300,
                description="the boot-stop page becoming ready",
            )
            initial = _evaluate(session, "arenaWeb.snapshot().status")
            _evaluate(
                session,
                """
                window.wp11Early = {};
                document.getElementById('start').addEventListener('click', () => {
                  window.wp11Early.start = arenaWeb.start().then(
                    () => { window.wp11Early.startResult = 'resolved'; },
                    (error) => { window.wp11Early.startResult = error.name; },
                  );
                  window.wp11Early.observedBooting = arenaWeb.snapshot().status === 'booting';
                  window.wp11Early.stopA = arenaWeb.stop();
                  window.wp11Early.stopB = arenaWeb.stop();
                  window.wp11Early.sameStop = window.wp11Early.stopA === window.wp11Early.stopB;
                  window.wp11Early.stopA.then((value) => { window.wp11Early.terminal = value; });
                }, {capture: true, once: true});
                """,
            )
            click_element(session, "start")
        wait_until(
            lambda: bool(_evaluate(session, "window.wp11Early?.terminal")),
            timeout=30,
            description=f"the {name} stop settling",
        )
        terminal = json.loads(
            _evaluate(session, "JSON.stringify(window.wp11Early.terminal)")
        )
        final = _snapshot(session)
        checks = [
            {
                "name": f"stop-during-{name}",
                "passed": initial == ("starting" if during_loading else "ready")
                and terminal
                == {"status": "exited", "exitCode": None, "reason": "host_stop"}
                and final.get("status") == "exited",
                "detail": {"initial": initial, "terminal": terminal},
            },
            {
                "name": f"duplicate-{name}-stop-same-promise",
                "passed": bool(_evaluate(session, "window.wp11Early.sameStop")),
            },
        ]
        if not during_loading:
            checks.append(
                {
                    "name": "booting-state-witnessed-before-stop",
                    "passed": bool(
                        _evaluate(session, "window.wp11Early.observedBooting")
                    ),
                }
            )
        return checks
    finally:
        if session is not None:
            session.close()
        browser.stop()


def run(
    chrome: Path,
    serve_dir: Path,
    output_dir: Path,
    *,
    engine_dir: Path,
    content_dir: Path,
    skip_stage: bool,
    headless: bool,
) -> dict:
    if skip_stage:
        verify_staged(ROOT, serve_dir)
    else:
        stage(ROOT, serve_dir, engine_dir=engine_dir, content_dir=content_dir)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    expected_version = pinned_browser_version(ROOT)
    version = ChromeProcess(chrome, output_dir / "version-profile").version()
    if f"Chrome for Testing {expected_version}" not in version:
        raise AcceptanceError(f"wrong browser: {version}")

    browser = ChromeProcess(
        chrome,
        output_dir / "profile",
        headless=headless,
        window_size=(1280, 720),
        angle_backend="gl",
    )
    checks: list[dict] = []
    session = None
    with StaticServe(serve_dir) as server:
        checks.extend(
            early_stop_checks(
                chrome,
                server,
                output_dir,
                during_loading=True,
                headless=headless,
            )
        )
        checks.extend(
            early_stop_checks(
                chrome,
                server,
                output_dir,
                during_loading=False,
                headless=headless,
            )
        )
        try:
            browser.start()
            session = browser.page_session()
            for domain in ("Page", "Runtime", "Log"):
                session.call(f"{domain}.enable")
            session.call("Page.navigate", {"url": f"{server.origin}/"})
            wait_until(
                lambda: _evaluate(session, "window.arenaWeb?.report?.status")
                in ("ready", "failed"),
                timeout=300,
                description="the loader becoming ready",
            )
            checks.append(
                {
                    "name": "ready",
                    "passed": _evaluate(session, "window.arenaWeb.report.status")
                    == "ready",
                }
            )

            # Capture listener runs before the button's own click handler. The
            # CDP pointer sequence is a trusted user gesture, so this proves the
            # public start path rather than a synthetic Element.click().
            _evaluate(
                session,
                """
                window.wp11 = { snapshots: [], duplicate: null, startResolved: false };
                window.wp11.unsubscribe = arenaWeb.subscribe((value) => {
                  window.wp11.snapshots.push(value);
                });
                document.getElementById('start').addEventListener('click', () => {
                  window.wp11.startPromise = arenaWeb.start().then(() => {
                    window.wp11.startResolved = true;
                  });
                  arenaWeb.start().then(
                    () => { window.wp11.duplicate = {resolved: true}; },
                    (error) => {
                      window.wp11.duplicate = {
                        resolved: false,
                        name: error.name,
                        message: error.message,
                      };
                    },
                  );
                }, {capture: true, once: true});
                """,
            )
            initial = json.loads(
                _evaluate(
                    session,
                    "JSON.stringify(window.wp11.snapshots.map((x) => x.status))",
                )
            )
            checks.append(
                {"name": "subscribe-immediate", "passed": initial == ["ready"]}
            )
            click_element(session, "start")
            wait_until(
                lambda: _evaluate(session, "window.wp11.duplicate !== null"),
                timeout=10,
                description="the duplicate start rejection",
            )
            duplicate = json.loads(
                _evaluate(session, "JSON.stringify(window.wp11.duplicate)")
            )
            checks.append(
                {
                    "name": "double-start-refused",
                    "passed": duplicate
                    == {
                        "resolved": False,
                        "name": "LoaderError",
                        "message": "Start has already been accepted",
                    },
                    "detail": duplicate,
                }
            )
            wait_until(
                lambda: _evaluate(
                    session,
                    "window.arenaWeb.snapshot().markers.clientGameLoaded !== undefined",
                ),
                timeout=300,
                description="the real engine reaching the map",
            )
            checks.append(
                {
                    "name": "host-start-resolved",
                    "passed": bool(_evaluate(session, "window.wp11.startResolved")),
                }
            )

            # The API result is exact. Unsubscribe before another publication,
            # then prove that focus cannot call the removed listener.
            before_unsubscribe = int(
                _evaluate(session, "window.wp11.snapshots.length") or 0
            )
            _evaluate(session, "window.wp11.unsubscribe(); window.wp11.unsubscribe()")
            focus = json.loads(
                _evaluate(session, "JSON.stringify(window.arenaWeb.focusSurface())")
            )
            after_unsubscribe = int(
                _evaluate(session, "window.wp11.snapshots.length") or 0
            )
            checks.extend(
                [
                    {
                        "name": "surface-focus",
                        "passed": focus
                        == {"ok": True, "focused": True, "reason": None},
                        "detail": focus,
                    },
                    {
                        "name": "unsubscribe-idempotent",
                        "passed": before_unsubscribe == after_unsubscribe,
                    },
                ]
            )

            click_element(session, "fullscreen")
            wait_until(
                lambda: bool(
                    _evaluate(
                        session,
                        "document.fullscreenElement === document.getElementById('stage')",
                    )
                ),
                timeout=20,
                description="the stage entering fullscreen",
            )
            checks.append({"name": "fullscreen-enter", "passed": True})
            click_element(session, "fullscreen")
            wait_until(
                lambda: not bool(_evaluate(session, "document.fullscreenElement")),
                timeout=20,
                description="the stage leaving fullscreen",
            )
            checks.append({"name": "fullscreen-leave", "passed": True})

            _evaluate(
                session,
                """
                window.wp11.settlementA = arenaWeb.whenSettled();
                window.wp11.settlementB = arenaWeb.whenSettled();
                window.wp11.sameSettlement = window.wp11.settlementA === window.wp11.settlementB;
                window.wp11.stopA = arenaWeb.stop();
                window.wp11.stopB = arenaWeb.stop();
                window.wp11.sameStop = window.wp11.stopA === window.wp11.stopB;
                window.wp11.terminal = null;
                window.wp11.stopA.then((value) => { window.wp11.terminal = value; });
                """,
            )
            wait_until(
                lambda: _evaluate(session, "window.wp11.terminal !== null"),
                timeout=30,
                description="the real engine exit settlement",
            )
            terminal = json.loads(
                _evaluate(session, "JSON.stringify(window.wp11.terminal)")
            )
            final = _snapshot(session)
            event_kinds = [item["kind"] for item in final.get("events", [])]
            checks.extend(
                [
                    {
                        "name": "duplicate-stop-same-promise",
                        "passed": bool(_evaluate(session, "window.wp11.sameStop")),
                    },
                    {
                        "name": "stable-settlement-promise",
                        "passed": bool(
                            _evaluate(session, "window.wp11.sameSettlement")
                        ),
                    },
                    {
                        "name": "real-host-stop-settlement",
                        "passed": terminal.get("status") == "exited"
                        and terminal.get("reason") == "host_stop"
                        and isinstance(terminal.get("exitCode"), int),
                        "detail": terminal,
                    },
                    {
                        "name": "engine-quit-and-exit-witnessed",
                        "passed": "engine-quit-requested" in event_kinds
                        and "engine-exit" in event_kinds,
                    },
                    {
                        "name": "final-snapshot-matches",
                        "passed": final.get("status") == "exited"
                        and final.get("exit")
                        == {
                            "code": terminal.get("exitCode"),
                            "reason": "host_stop",
                        },
                    },
                    {
                        "name": "no-browser-error",
                        "passed": not final.get("browserErrors"),
                        "detail": final.get("browserErrors"),
                    },
                ]
            )
        finally:
            if session is not None:
                session.close()
            browser.stop()

    result = {
        "browser": version,
        "headless": headless,
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", type=Path, required=True)
    parser.add_argument(
        "--engine-dir", type=Path, default=ROOT / "build/browser/tree/Release"
    )
    parser.add_argument(
        "--content-dir", type=Path, default=ROOT / "build/content-pack"
    )
    parser.add_argument(
        "--serve-dir", type=Path, default=ROOT / "build/arena-serve"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "build/host-lifecycle-acceptance"
    )
    parser.add_argument("--skip-stage", action="store_true")
    parser.add_argument("--headed", action="store_true")
    arguments = parser.parse_args()
    try:
        result = run(
            arguments.chrome.resolve(),
            arguments.serve_dir.resolve(),
            arguments.output_dir.resolve(),
            engine_dir=arguments.engine_dir.resolve(),
            content_dir=arguments.content_dir.resolve(),
            skip_stage=arguments.skip_stage,
            headless=not arguments.headed,
        )
    except (AcceptanceError, ArenaRuntimeError, BrowserSessionError, TimeoutError) as error:
        print(f"host lifecycle acceptance could not run: {error}", file=sys.stderr)
        return 2
    for check in result["checks"]:
        print(f"[{'ok' if check['passed'] else 'FAIL'}] {check['name']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
