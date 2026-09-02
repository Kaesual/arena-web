#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure the bounded WP11 server profile under idle and two-client load.

The probe is deliberately small. It runs the exact committed server command in
the verified image with the candidate cgroup/tmpfs ceilings, samples the
server container's cgroup and writable home, drives two matching native clients
through ordinary console input, then witnesses graceful stop and an unexpected
process exit. It emits no host name, container address or credential.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from arena_server import load_profile, server_launch_arguments  # noqa: E402
from census_run import (  # noqa: E402
    CLIENT_BINARIES,
    PLAY_CYCLE,
    SELECT_BEST_WEAPON,
    _stage_client_root,
)

MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
WRITABLE_LIMIT_BYTES = 64 * 1024 * 1024
CPU_LIMIT_CORES = 1
PIDS_LIMIT = 128
STARTUP_DEADLINE_SECONDS = 20
HEALTH_INTERVAL_SECONDS = 1
HEALTH_FAILURE_THRESHOLD = 3
STOP_TIMEOUT_SECONDS = 10
IDLE_SECONDS = 10
BUSY_SECONDS = 30
SAMPLE_INTERVAL_SECONDS = 0.5
CHALLENGE = "a11ce55"


class ProbeError(RuntimeError):
    pass


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


def _identity(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _json(command: list[str]) -> Any:
    return json.loads(_run(command).stdout)


def parse_getstatus(
    payload: bytes,
    rotation: list[str],
    profile: dict[str, Any],
    challenge: str = CHALLENGE,
    *,
    started: bool = False,
) -> dict[str, str]:
    prefix = b"\xff\xff\xff\xffstatusResponse\n"
    if not payload.startswith(prefix):
        raise ProbeError("health reply has no statusResponse prefix")
    line = payload[len(prefix) :].split(b"\n", 1)[0]
    try:
        text = line.decode("ascii")
    except UnicodeDecodeError as error:
        raise ProbeError("health info string is not ASCII") from error
    if not text.startswith("\\"):
        raise ProbeError("health info string has no leading separator")
    parts = text[1:].split("\\")
    if len(parts) % 2 != 0 or any(part == "" for part in parts):
        raise ProbeError("health info string is malformed")
    values = dict(zip(parts[0::2], parts[1::2], strict=True))
    # Read out of the profile rather than restated. `check_match_end_cvars`
    # now permits any `fraglimit`/`timelimit` pair that is not both zero, so a
    # legal profile change would leave literals here quietly wrong — the same
    # remembered-measurement shape this repository refused for the systeminfo
    # allowance.
    cvars = profile["cvars"]
    required = {
        "challenge": challenge,
        "g_gametype": cvars["g_gametype"],
        "fraglimit": cvars["fraglimit"],
        "timelimit": cvars["timelimit"],
        "sv_maxclients": cvars["sv_maxclients"],
    }
    # **Readiness and liveness ask different things of `mapname`, and
    # conflating them would fail every rotating server.** `SV_SpawnServer` sets
    # `mapname` afresh on every map change (ioq3 code/server/sv_init.c), which
    # is the whole point of a rotation — so a check that pins it to the
    # rotation's first entry is right exactly once, at readiness, where it
    # proves the launch argument took effect and therefore that a rotation was
    # supplied at all. Applied again a second later it would declare a server
    # failed for doing what it was asked to do. After readiness the map only
    # has to still be one of the rotation's own.
    required["mapname"] = rotation[0] if not started else None
    for name, expected in required.items():
        if expected is None:
            continue
        if values.get(name) != expected:
            raise ProbeError(f"health field {name} does not match the profile")
    if started:
        if values.get("mapname") not in rotation:
            raise ProbeError(
                f"health field mapname is '{values.get('mapname')}', which is not "
                f"in the rotation {rotation}"
            )
        required["mapname"] = values["mapname"]
    return {name: values[name] for name in required}


def health(
    socket_: socket.socket,
    endpoint: tuple[str, int],
    rotation: list[str],
    profile: dict[str, Any],
    *,
    started: bool = False,
) -> dict[str, str]:
    socket_.sendto(b"\xff\xff\xff\xffgetstatus " + CHALLENGE.encode() + b"\n", endpoint)
    payload, source = socket_.recvfrom(65535)
    if source != endpoint:
        raise ProbeError("health reply came from another endpoint")
    return parse_getstatus(payload, rotation, profile, started=started)


def observation(*, exists: bool, running: bool, ready: bool, within_deadline: bool, failures: int) -> str:
    if not exists:
        return "missing"
    if not running:
        return "failed"
    if ready and failures < HEALTH_FAILURE_THRESHOLD:
        return "ready"
    if within_deadline and not ready:
        return "preparing"
    if failures >= HEALTH_FAILURE_THRESHOLD or not within_deadline:
        return "failed"
    return "preparing"


def _cgroup_path(pid: int) -> Path:
    for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines():
        hierarchy, controllers, relative = line.split(":", 2)
        if hierarchy == "0" and controllers == "":
            path = Path("/sys/fs/cgroup") / relative.lstrip("/")
            if path.is_dir():
                return path
    raise ProbeError("server container has no readable cgroup-v2 path")


def _integer(path: Path) -> int:
    text = path.read_text(encoding="ascii").strip()
    if text == "max":
        raise ProbeError(f"{path.name} is unexpectedly unlimited")
    return int(text)


def _cpu_usage(cgroup: Path) -> int:
    values = {}
    for line in (cgroup / "cpu.stat").read_text(encoding="ascii").splitlines():
        name, value = line.split()
        values[name] = int(value)
    return values["usage_usec"]


def _process_memory(pid: int) -> tuple[int, int]:
    values = {}
    for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            values[name] = value.strip()

    def kib(name: str) -> int:
        fields = values[name].split()
        if len(fields) != 2 or fields[1] != "kB":
            raise ProbeError(f"/proc status {name} is malformed")
        return int(fields[0]) * 1024

    return kib("VmRSS"), kib("VmHWM")


def _home_bytes(runtime: str, name: str) -> int:
    result = _run([runtime, "exec", name, "du", "-sb", "/var/lib/arena"])
    return int(result.stdout.split()[0])


@dataclass
class PhaseSampler:
    runtime: str
    container: str
    pid: int
    cgroup: Path
    started: float
    started_cpu_usec: int
    last_at: float
    last_cpu_usec: int
    samples: int = 0
    peak_cpu_cores: float = 0
    peak_memory_current: int = 0
    peak_process_rss: int = 0
    peak_process_hwm: int = 0
    peak_home_bytes: int = 0

    @classmethod
    def begin(cls, runtime: str, container: str) -> "PhaseSampler":
        pid = int(_run([runtime, "inspect", "--format", "{{.State.Pid}}", container]).stdout)
        cgroup = _cgroup_path(pid)
        now = time.monotonic()
        cpu = _cpu_usage(cgroup)
        return cls(runtime, container, pid, cgroup, now, cpu, now, cpu)

    def sample(self) -> None:
        now = time.monotonic()
        cpu = _cpu_usage(self.cgroup)
        elapsed = now - self.last_at
        if elapsed > 0:
            self.peak_cpu_cores = max(
                self.peak_cpu_cores,
                ((cpu - self.last_cpu_usec) / 1_000_000) / elapsed,
            )
        rss, hwm = _process_memory(self.pid)
        self.peak_memory_current = max(
            self.peak_memory_current, _integer(self.cgroup / "memory.current")
        )
        self.peak_process_rss = max(self.peak_process_rss, rss)
        self.peak_process_hwm = max(self.peak_process_hwm, hwm)
        self.peak_home_bytes = max(
            self.peak_home_bytes, _home_bytes(self.runtime, self.container)
        )
        self.samples += 1
        self.last_at = now
        self.last_cpu_usec = cpu

    def result(self) -> dict[str, Any]:
        ended = time.monotonic()
        cpu = _cpu_usage(self.cgroup)
        elapsed = ended - self.started
        return {
            "durationSeconds": round(elapsed, 3),
            "samples": self.samples,
            "cpuSeconds": round((cpu - self.started_cpu_usec) / 1_000_000, 6),
            "meanCpuCores": round(((cpu - self.started_cpu_usec) / 1_000_000) / elapsed, 6),
            "peakSampleCpuCores": round(self.peak_cpu_cores, 6),
            "peakCgroupMemoryBytes": max(
                self.peak_memory_current, _integer(self.cgroup / "memory.peak")
            ),
            "peakProcessRssBytes": self.peak_process_rss,
            "peakProcessHwmBytes": self.peak_process_hwm,
            "peakWritableHomeBytes": self.peak_home_bytes,
        }


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as socket_:
        socket_.bind(("127.0.0.1", 0))
        return socket_.getsockname()[1]


def _wait_ready(
    socket_: socket.socket,
    endpoint: tuple[str, int],
    rotation: list[str],
    profile: dict[str, Any],
) -> tuple[float, dict[str, str]]:
    started = time.monotonic()
    last_error = None
    while time.monotonic() - started < STARTUP_DEADLINE_SECONDS:
        try:
            return time.monotonic() - started, health(socket_, endpoint, rotation, profile)
        except (OSError, ProbeError) as error:
            last_error = error
            time.sleep(HEALTH_INTERVAL_SECONDS)
    raise ProbeError(f"server missed its readiness deadline: {last_error}")


def _start_server(
    runtime: str,
    name: str,
    network: str,
    address: str,
    host_port: int,
    image: str,
    arguments: list[str],
) -> None:
    _run(
        [
            runtime,
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            network,
            "--ip",
            address,
            "--publish",
            f"127.0.0.1:{host_port}:27960/udp",
            "--cap-drop",
            "all",
            "--security-opt",
            "label=disable",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--memory",
            str(MEMORY_LIMIT_BYTES),
            "--cpus",
            str(CPU_LIMIT_CORES),
            "--pids-limit",
            str(PIDS_LIMIT),
            "--tmpfs",
            f"/var/lib/arena:rw,noexec,nosuid,nodev,mode=1777,size={WRITABLE_LIMIT_BYTES}",
            image,
            *arguments,
        ]
    )


def _start_client(
    runtime: str,
    name: str,
    network: str,
    address: str,
    toolchain: str,
    root: Path,
    profile: dict[str, Any],
    player: str,
    server_endpoint: str,
    log: Path,
) -> tuple[subprocess.Popen[bytes], Any]:
    stream = log.open("wb")
    command = [
        runtime,
        "run",
        "--rm",
        "--interactive",
        "--name",
        name,
        "--network",
        network,
        "--ip",
        address,
        "--cap-drop",
        "all",
        "--security-opt",
        "label=disable",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "0:0",
        "--env",
        "ARENA_DISPLAY=:99",
        "--env",
        "ARENA_SCREEN=640x480x24",
        "--env",
        "ARENA_CLIENT_BINARY=/opt/arena-web/ioquake3",
        "--env",
        "HOME=/var/lib/arena",
        "--volume",
        f"{root}:/opt/arena-web:ro",
        "--volume",
        f"{ROOT / 'native/census-client.sh'}:/census-client.sh:ro",
        "--tmpfs",
        "/var/lib/arena:rw,nosuid,nodev,mode=1777",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,mode=1777",
        "--entrypoint",
        "/bin/sh",
        toolchain,
        "/census-client.sh",
        *profile["clientArguments"],
        "+set",
        "name",
        player,
        "+connect",
        server_endpoint,
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT)
    return process, stream


def _send(process: subprocess.Popen[bytes], command: str) -> None:
    if process.poll() is not None or process.stdin is None:
        raise ProbeError("a resource-probe client exited early")
    process.stdin.write(f"{command}\n".encode())
    process.stdin.flush()


def _wait_players(runtime: str, server: str, players: tuple[str, str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = _run([runtime, "logs", server], check=False)
        logs = result.stdout + result.stderr
        if all(
            f"{player} entered the game" in logs
            or f"{player}^7 entered the game" in logs
            for player in players
        ):
            return
        time.sleep(0.5)
    raise ProbeError("both resource-probe clients did not enter the game")


def _sample_for(sampler: PhaseSampler, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sampler.sample()
        time.sleep(min(SAMPLE_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))


def _busy_sample(sampler: PhaseSampler, clients: list[subprocess.Popen[bytes]]) -> None:
    commands = [*SELECT_BEST_WEAPON, *(command for _, command in PLAY_CYCLE)]
    deadline = time.monotonic() + BUSY_SECONDS
    index = 0
    while time.monotonic() < deadline:
        command = commands[index % len(commands)]
        for client in clients:
            _send(client, command)
        index += 1
        sampler.sample()
        time.sleep(min(SAMPLE_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
    for client in clients:
        for command in ("-attack", "-forward", "-left", "-right", "-moveleft", "-moveright"):
            _send(client, command)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=os.environ.get("CONTAINER_RUNTIME", "podman"))
    parser.add_argument("--server-image", default="arena-web-server:latest")
    parser.add_argument("--output", type=Path, default=ROOT / "build/server-resource-probe.json")
    parser.add_argument("--client-dir", type=Path, default=ROOT / "build/native-client/tree/Release")
    # Required, with no default. The map a server plays is a launch argument and
    # this probe is a caller like any other, so it makes the choice explicitly
    # rather than inheriting one — the same reason the loader refuses a page
    # opened without ?maps=.
    parser.add_argument("--rotation", required=True)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    if (ROOT / "build").resolve() not in output.parents:
        raise ProbeError("--output must be below build/")

    runtime = arguments.runtime
    profile = load_profile(ROOT)
    rotation = [name.strip() for name in arguments.rotation.split(",")]
    toolchain = _run([str(ROOT / "scripts/build-native-toolchain.sh"), "--print-tag"]).stdout.strip()
    client_dir = arguments.client_dir.resolve()
    for name in CLIENT_BINARIES:
        if not (client_dir / name).is_file():
            raise ProbeError(f"{client_dir / name} does not exist; build the native client first")

    image = arguments.server_image
    image_id = _run([runtime, "image", "inspect", "--format", "{{.Id}}", image]).stdout.strip()
    image_size = int(_run([runtime, "image", "inspect", "--format", "{{.Size}}", image]).stdout)
    session = f"arena-resource-{os.getpid()}"
    network = session
    server = f"{session}-server"
    failed_server = f"{session}-failed"
    client_names = [f"{session}-client-a", f"{session}-client-b"]
    server_ip = "10.203.0.10"
    client_ips = ("10.203.0.20", "10.203.0.21")
    host_port = _free_udp_port()
    client_root = ROOT / "build" / session / "client-root"
    shutil.rmtree(client_root.parent, ignore_errors=True)
    client_root.parent.mkdir(parents=True)
    _stage_client_root(client_root, profile, client_dir)
    clients: list[subprocess.Popen[bytes]] = []
    streams = []
    created_network = False
    started: list[str] = []

    try:
        _run([runtime, "network", "create", "--subnet", "10.203.0.0/24", network])
        created_network = True
        launch = server_launch_arguments(ROOT, profile, rotation)
        _start_server(runtime, server, network, server_ip, host_port, image, launch)
        started.append(server)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as health_socket:
            health_socket.bind(("127.0.0.1", 0))
            health_socket.settimeout(0.75)
            startup_seconds, health_fields = _wait_ready(
                health_socket, ("127.0.0.1", host_port), rotation, profile
            )

            idle = PhaseSampler.begin(runtime, server)
            _sample_for(idle, IDLE_SECONDS)
            idle_result = idle.result()

            players = ("ArenaResourceA", "ArenaResourceB")
            for index, (name, address, player) in enumerate(
                zip(client_names, client_ips, players, strict=True)
            ):
                process, stream = _start_client(
                    runtime,
                    name,
                    network,
                    address,
                    toolchain,
                    client_root,
                    profile,
                    player,
                    f"{server_ip}:27960",
                    client_root.parent / f"client-{index}.log",
                )
                clients.append(process)
                streams.append(stream)
                started.append(name)
            _wait_players(runtime, server, players)
            busy = PhaseSampler.begin(runtime, server)
            _busy_sample(busy, clients)
            busy_result = busy.result()
            time.sleep(HEALTH_INTERVAL_SECONDS)
            # The post-readiness check, which is the liveness one: the server
            # has been playing for a minute and may legitimately have rotated.
            health(
                health_socket,
                ("127.0.0.1", host_port),
                rotation,
                profile,
                started=True,
            )

        for client in clients:
            _send(client, "quit")
        for client in clients:
            client.wait(timeout=15)
        for stream in streams:
            stream.close()

        stop_started = time.monotonic()
        stopped = _run([runtime, "stop", "--time", str(STOP_TIMEOUT_SECONDS), server])
        stop_seconds = time.monotonic() - stop_started
        del stopped
        stopped_inspect = _json([runtime, "inspect", server])[0]
        graceful_exit = stopped_inspect["State"]["ExitCode"]
        if graceful_exit != 1 or stop_seconds > STOP_TIMEOUT_SECONDS:
            raise ProbeError("the graceful server stop contract did not hold")
        size_rw = int(
            _run([runtime, "inspect", "--size", "--format", "{{.SizeRw}}", server]).stdout
        )
        _run([runtime, "rm", server])
        started.remove(server)

        # A separate ready instance is killed without an operator stop. Its
        # immediate non-running observation is the real failed-state witness.
        host_port = _free_udp_port()
        _start_server(
            runtime,
            failed_server,
            network,
            server_ip,
            host_port,
            image,
            launch,
        )
        started.append(failed_server)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as health_socket:
            health_socket.bind(("127.0.0.1", 0))
            health_socket.settimeout(0.75)
            _wait_ready(health_socket, ("127.0.0.1", host_port), rotation, profile)
        _run([runtime, "kill", "--signal", "KILL", failed_server])
        failed_inspect = _json([runtime, "inspect", failed_server])[0]
        failed_exit = failed_inspect["State"]["ExitCode"]
        if failed_exit == 0:
            raise ProbeError("the unexpected-exit witness returned success")

        maximums = {
            "peakCgroupMemoryBytes": max(
                idle_result["peakCgroupMemoryBytes"], busy_result["peakCgroupMemoryBytes"]
            ),
            "peakProcessRssBytes": max(
                idle_result["peakProcessRssBytes"], busy_result["peakProcessRssBytes"]
            ),
            "peakWritableHomeBytes": max(
                idle_result["peakWritableHomeBytes"], busy_result["peakWritableHomeBytes"]
            ),
            "peakSampleCpuCores": max(
                idle_result["peakSampleCpuCores"], busy_result["peakSampleCpuCores"]
            ),
        }
        if maximums["peakCgroupMemoryBytes"] >= MEMORY_LIMIT_BYTES:
            raise ProbeError("the candidate memory ceiling was reached")
        if maximums["peakWritableHomeBytes"] >= WRITABLE_LIMIT_BYTES:
            raise ProbeError("the candidate writable ceiling was reached")

        record = {
            "formatVersion": 1,
            # The day this ran. It was a literal, which meant the record kept
            # claiming the day the literal was last edited — a small instance
            # of the same shape as a document restating a value nothing checks.
            "measuredAt": datetime.date.today().isoformat(),
            "release": {
                # The commit that produced the *server manifest*, taken from
                # the manifest's own producer record rather than from HEAD. A
                # probe re-run against an unchanged image — which is what a
                # reissue that moves no content does — must not restamp this
                # with whatever commit happened to be checked out, or the field
                # would name a commit that built nothing.
                "serverManifestCommit": json.loads(
                    (ROOT / "provenance/arena-web-server.json").read_text(
                        encoding="utf-8"
                    )
                )["producer"]["commit"],
                "engineCommit": _run(
                    [sys.executable, str(ROOT / "scripts/baseline-inputs.py"), "engine-commit"]
                ).stdout.strip(),
                "serverArtifactManifest": _identity(ROOT / "provenance/arena-web-server.json"),
                "serverImageId": f"sha256:{image_id.removeprefix('sha256:')}",
            },
            "profile": {
                "humans": 2,
                "bots": 3,
                "slots": 8,
                "rotation": list(rotation),
                "udpPort": profile["port"],
                "busyTraffic": "ordinary native-client movement, weapon selection, fire, chat and respawn commands",
            },
            "candidateAndAcceptedLimits": {
                "cpuCores": CPU_LIMIT_CORES,
                "memoryBytes": MEMORY_LIMIT_BYTES,
                "pids": PIDS_LIMIT,
                "writableHomeBytes": WRITABLE_LIMIT_BYTES,
                "writableHomePath": "/var/lib/arena",
            },
            "measurement": {
                "startupReadySeconds": round(startup_seconds, 3),
                "healthFields": health_fields,
                "idle": idle_result,
                "busy": busy_result,
                "maximums": maximums,
                "readOnlyImageBytes": image_size,
                "containerWritableLayerBytesAfterStop": size_rw,
            },
            "headroom": {
                "cpuCores": CPU_LIMIT_CORES - maximums["peakSampleCpuCores"],
                "cpuFactor": round(
                    CPU_LIMIT_CORES / maximums["peakSampleCpuCores"], 3
                ),
                "memoryBytes": MEMORY_LIMIT_BYTES - maximums["peakCgroupMemoryBytes"],
                "memoryFactor": round(MEMORY_LIMIT_BYTES / maximums["peakCgroupMemoryBytes"], 3),
                "writableHomeBytes": WRITABLE_LIMIT_BYTES - maximums["peakWritableHomeBytes"],
                "writableHomeFactor": round(
                    WRITABLE_LIMIT_BYTES / maximums["peakWritableHomeBytes"], 3
                ),
            },
            "lifecycle": {
                "startupDeadlineSeconds": STARTUP_DEADLINE_SECONDS,
                "healthIntervalSeconds": HEALTH_INTERVAL_SECONDS,
                "postReadyFailureThreshold": HEALTH_FAILURE_THRESHOLD,
                "stopTimeoutSeconds": STOP_TIMEOUT_SECONDS,
                "gracefulStopSeconds": round(stop_seconds, 3),
                "gracefulExitCode": graceful_exit,
                "unexpectedExitCode": failed_exit,
                "witnessed": ["preparing", "ready", "failed", "missing"],
            },
            "observations": {
                "preparing": "a desired process is running within the startup deadline but has no valid readiness reply",
                "ready": "the exact binary getstatus profile passes; after readiness, fewer than three consecutive one-second checks have failed",
                "failed": "the owned process exited unexpectedly, missed its startup deadline, or failed three consecutive post-ready checks",
                "missing": "no owned runtime exists",
            },
            "scope": "eight-slot prototype capacity guard for the accepted two-human, three-bot profile; not an SLO or larger-capacity claim",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {output}")
        print(json.dumps(record["measurement"]["maximums"], sort_keys=True))
        return 0
    finally:
        for process in clients:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for stream in streams:
            if not stream.closed:
                stream.close()
        for name in reversed(started):
            _run([runtime, "rm", "--force", name], check=False)
        if created_network:
            _run([runtime, "network", "rm", network], check=False)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProbeError, subprocess.CalledProcessError) as error:
        print(f"resource probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
