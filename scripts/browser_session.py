# SPDX-License-Identifier: GPL-2.0-or-later
"""A dependency-free driver for the pinned acceptance browser.

WP4's automated pre-acceptance has to drive the exact WP0 Chrome for Testing
build, and this repository takes no third-party dependency, so the three pieces
it needs are implemented here on the standard library:

* :class:`WebSocketClient` — the client half of RFC 6455 that the Chrome
  DevTools Protocol endpoint speaks;
* :class:`DevToolsSession` — request/response correlation and an event queue on
  top of it;
* :class:`ChromeProcess` — launching the pinned binary against a throwaway
  profile and finding its DevTools endpoint.

None of this is product code. It exists to produce evidence, and it is
deliberately small: it speaks only the subset of the protocol the acceptance
run uses.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA

# One DevTools message can carry a base64 screenshot, so the ceiling is
# generous; it exists so a malformed length cannot make the driver allocate
# without bound.
MAX_FRAME_BYTES = 64 * 1024 * 1024


class BrowserSessionError(RuntimeError):
    """The browser could not be launched, reached or driven."""


class WebSocketClient:
    """The minimum RFC 6455 client the DevTools endpoint needs."""

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        parts = urlsplit(url)
        if parts.scheme != "ws":
            raise BrowserSessionError(f"only ws:// endpoints are supported, got {url}")
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        self._buffer = bytearray()
        self._closed = False
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._socket.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        try:
            self._socket.sendall(request.encode("ascii"))
            self._handshake(key)
        except BaseException:
            # A refused upgrade must not leave the connection open behind the
            # exception; nothing else will ever hold a reference to it.
            self.close()
            raise

    def _handshake(self, key: str) -> None:
        header = self._read_until(b"\r\n\r\n")
        text = header.decode("latin-1")
        status = text.split("\r\n", 1)[0]
        if "101" not in status:
            raise BrowserSessionError(f"websocket upgrade refused: {status}")
        expected = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        accept = None
        for line in text.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "sec-websocket-accept":
                accept = value.strip()
        if accept != expected:
            raise BrowserSessionError("websocket upgrade returned a wrong accept key")

    def _read_until(self, terminator: bytes) -> bytes:
        while terminator not in self._buffer:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise BrowserSessionError("the browser closed the connection")
            self._buffer += chunk
        index = self._buffer.index(terminator) + len(terminator)
        head = bytes(self._buffer[:index])
        del self._buffer[:index]
        return head

    def _read_exactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise BrowserSessionError("the browser closed the connection")
            self._buffer += chunk
        head = bytes(self._buffer[:count])
        del self._buffer[:count]
        return head

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x80 | OPCODE_TEXT])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = secrets.token_bytes(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(bytes(header) + masked)

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exactly(2)
        final = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", self._read_exactly(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", self._read_exactly(8))
        if length > MAX_FRAME_BYTES:
            raise BrowserSessionError(f"refusing a {length}-byte websocket frame")
        mask = self._read_exactly(4) if masked else b""
        payload = self._read_exactly(length)
        if masked:
            payload = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(payload)
            )
        return final, opcode, payload

    def receive_text(self, timeout: float) -> str:
        """Return the next complete text message, answering control frames."""
        deadline = time.monotonic() + timeout
        fragments = bytearray()
        collecting = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no DevTools message arrived in time")
            self._socket.settimeout(remaining)
            final, opcode, payload = self._read_frame()
            if opcode == OPCODE_PING:
                self._send_control(OPCODE_PONG, payload)
                continue
            if opcode == OPCODE_PONG:
                continue
            if opcode == OPCODE_CLOSE:
                self._closed = True
                raise BrowserSessionError("the browser closed the DevTools session")
            if opcode == OPCODE_BINARY:
                raise BrowserSessionError("the DevTools endpoint sent a binary frame")
            if opcode == OPCODE_TEXT:
                fragments = bytearray(payload)
                collecting = True
            elif opcode == OPCODE_CONTINUATION:
                if not collecting:
                    raise BrowserSessionError("continuation frame without a start")
                fragments += payload
            if final and collecting:
                return fragments.decode("utf-8")

    def _send_control(self, opcode: int, payload: bytes) -> None:
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(
            bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + masked
        )

    def close(self) -> None:
        if not self._closed:
            try:
                self._send_control(OPCODE_CLOSE, b"\x03\xe8")
            except OSError:
                pass
            self._closed = True
        try:
            self._socket.close()
        except OSError:
            pass


class DevToolsSession:
    """Correlated calls and a buffered event stream over one DevTools socket."""

    def __init__(self, websocket_url: str, *, timeout: float = 30.0) -> None:
        self._socket = WebSocketClient(websocket_url, timeout=timeout)
        self._next_id = 0
        self._events: deque[dict[str, Any]] = deque()
        self.timeout = timeout

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        self._socket.send_text(
            json.dumps({"id": message_id, "method": method, "params": params or {}})
        )
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{method} did not answer in time")
            message = json.loads(self._socket.receive_text(remaining))
            if message.get("id") != message_id:
                if "method" in message:
                    self._events.append(message)
                continue
            if "error" in message:
                raise BrowserSessionError(f"{method} failed: {message['error']}")
            return message.get("result", {})

    def pump(self, duration: float) -> None:
        """Read events for `duration` seconds without issuing a call."""
        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                message = json.loads(self._socket.receive_text(remaining))
            except TimeoutError:
                return
            if "method" in message:
                self._events.append(message)

    def drain_events(self) -> list[dict[str, Any]]:
        events = list(self._events)
        self._events.clear()
        return events

    def close(self) -> None:
        self._socket.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ChromeProcess:
    """The pinned Chrome for Testing binary against a throwaway profile."""

    def __init__(
        self,
        binary: Path,
        user_data_dir: Path,
        *,
        headless: bool = True,
        window_size: tuple[int, int] = (1280, 720),
        angle_backend: str = "gl",
        extra_arguments: tuple[str, ...] = (),
    ) -> None:
        self.binary = binary
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.window_size = window_size
        self.angle_backend = angle_backend
        self.extra_arguments = extra_arguments
        self.process: subprocess.Popen[bytes] | None = None
        self.devtools_port: int | None = None
        self.arguments: list[str] = []
        self.stderr_path = user_data_dir.parent / f"{user_data_dir.name}-chrome.log"

    def version(self) -> str:
        result = subprocess.run(
            [str(self.binary), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return result.stdout.strip()

    def start(self) -> None:
        if self.user_data_dir.exists():
            shutil.rmtree(self.user_data_dir)
        self.user_data_dir.mkdir(parents=True)
        port = _free_port()
        arguments = [
            str(self.binary),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.user_data_dir}",
            f"--window-size={self.window_size[0]},{self.window_size[1]}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=Translate,MediaRouter",
            # The slice is offline evidence, so the browser's own background
            # traffic — sync, component updates, GCM registration — is switched
            # off and every request the run records belongs to the page.
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-client-side-phishing-detection",
            "--disable-default-apps",
            "--disable-sync",
            "--no-service-autorun",
            "--metrics-recording-only",
            "--password-store=basic",
            "--use-mock-keychain",
            *self.extra_arguments,
            "about:blank",
        ]
        if self.headless:
            # WebGL has to work without a display. The ANGLE backend is a
            # choice the run records: 'swiftshader' is Chrome's own software
            # rasteriser and needs an explicit opt-in since Chrome 137, while a
            # hardware backend uses the host's real driver. A headed run leaves
            # the choice to Chrome, which is what a player would get.
            headless_arguments = ["--headless=new", f"--use-angle={self.angle_backend}"]
            if self.angle_backend == "swiftshader":
                headless_arguments.append("--enable-unsafe-swiftshader")
            arguments[1:1] = headless_arguments
        self.arguments = arguments
        environment = dict(os.environ)
        environment.setdefault("LC_ALL", "C")
        with self.stderr_path.open("wb") as stderr:
            self.process = subprocess.Popen(  # noqa: S603
                arguments,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                env=environment,
            )
        self.devtools_port = self._await_devtools(port)

    def _await_devtools(self, port: int, timeout: float = 60.0) -> int:
        """Wait until the DevTools HTTP endpoint answers on the chosen port.

        The port is chosen here rather than by the browser, so the endpoint
        itself is the readiness signal; the `DevToolsActivePort` marker file is
        not relied upon.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise BrowserSessionError(
                    f"the browser exited with status {self.process.returncode}; "
                    f"see {self.stderr_path}"
                )
            try:
                with urllib.request.urlopen(  # noqa: S310
                    f"http://127.0.0.1:{port}/json/version", timeout=2
                ) as response:
                    if response.status == 200:
                        return port
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise BrowserSessionError(f"the browser did not open a DevTools port on {port}")

    def targets(self) -> list[dict[str, Any]]:
        url = f"http://127.0.0.1:{self.devtools_port}/json/list"
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def page_session(self, *, timeout: float = 30.0) -> DevToolsSession:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for target in self.targets():
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return DevToolsSession(
                        target["webSocketDebuggerUrl"], timeout=timeout
                    )
            time.sleep(0.1)
        raise BrowserSessionError("the browser opened no page target")

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self.process.kill()
            self.process.wait(timeout=20)
        self.process = None


def wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float,
    interval: float = 0.25,
    description: str = "condition",
) -> Any:
    """Poll `predicate` until it returns something truthy or the time is up."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise TimeoutError(f"{description} did not happen within {timeout:.0f}s")
