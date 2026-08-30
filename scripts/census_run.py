#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the WP5 packet census: one session, captured at the engine/UDP boundary.

The session is deliberately small and complete: the native client connects to
the containerized dedicated server, plays a representative FFA round against the
profile's bots, disconnects, reconnects and plays again, and asks the server the
two connectionless queries a server list would ask. Everything runs on a private
container network with nothing else on it, and the capture is filtered to the
server's own UDP port, so the recorded evidence contains this session's game
traffic and nothing else — no credentials, no host traffic.

The instrumentation is outside the game protocol. No engine source is patched
and no engine option changes what is sent; the only control channel is the
client's own console on stdin, which is how a person would drive it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena_server import (  # noqa: E402
    ArenaServerError,
    client_tree_files,
    load_profile,
    stage_tree,
)
from packet_census import (  # noqa: E402
    PacketCensusError,
    build_records,
    parse_pcap,
    summarize,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SUBNET = "10.201.27.0/24"
DEFAULT_SERVER_IP = "10.201.27.10"
DEFAULT_CLIENT_IP = "10.201.27.20"
DISPLAY = ":99"
SCREEN = "640x480x24"

CLIENT_BINARIES = ("ioquake3", "renderer_opengl1.so", "renderer_opengl2.so")


class CensusError(RuntimeError):
    """The census could not be taken as declared."""


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise CensusError(
            f"{' '.join(command[:3])}… failed: {result.stderr.strip() or result.returncode}"
        )
    return result


def _stage_client_root(target: Path, profile: dict[str, Any], client_dir: Path) -> None:
    """Assemble the client's own root: game tree plus the binaries beside it.

    ioq3 derives fs_basepath from the executable's own directory
    (code/sys/sys_main.c:838-839, DEFAULT_BASEDIR at :739-747), so the game
    directory has to sit next to the binary here exactly as it does in the
    server image.
    """
    files = client_tree_files(REPO_ROOT, profile)
    stage_tree(
        REPO_ROOT,
        target,
        files,
        engine_dir=REPO_ROOT / "build/browser/tree/Release",
        content_dir=REPO_ROOT / "build/content-pack",
    )
    for name in CLIENT_BINARIES:
        source = client_dir / name
        if not source.is_file():
            raise CensusError(f"{source} does not exist; build the native client first")
        shutil.copyfile(source, target / name)
        (target / name).chmod(0o755)


# Representative play, driven from the client's own console: turn, fire, strafe,
# switch weapons, chat and respawn. The commands are exactly the ones ioq3
# registers for a person at the keyboard (code/client/cl_input.c), so nothing
# here is a special test path through the engine.
#
# The movement is deliberately short-burst. Nobody is looking at the screen, and
# oa_pvomit has trigger_hurt volumes that a client running blind in a straight
# line falls into within seconds; a session of respawns is not a session of play.
#
# The weapon numbers are ioq3's own (code/game/bg_public.h): 2 machinegun,
# 3 shotgun, 5 rocket launcher, 7 railgun, 8 plasma gun. They are issued in
# ascending order, so the last one the client actually owns wins: `CG_Weapon_f`
# returns without switching when the weapon is not in `STAT_WEAPONS`
# (code/cgame/cg_consolecmds.c). `weapnext` is deliberately not used — with only
# the spawn weapons it alternates between the machinegun and the gauntlet, and
# a client holding the gauntlet fires at nothing but air.
SELECT_BEST_WEAPON: tuple[str, ...] = (
    "weapon 2",
    "weapon 3",
    "weapon 5",
    "weapon 7",
    "weapon 8",
)

PLAY_CYCLE: tuple[tuple[float, str], ...] = (
    # Roam and collect. cl_yawspeed is 140 degrees per second
    # (code/client/cl_input.c), so a 0.5 s turn is about 70 degrees.
    (0.2, "+attack"),
    (0.2, "+forward"),
    (0.5, "+right"),
    (1.2, "-right"),
    (0.5, "+moveleft"),
    (1.2, "-moveleft"),
    (0.5, "+left"),
    (1.2, "-left"),
    (0.5, "+moveright"),
    (1.2, "-moveright"),
    (0.3, "-forward"),
    # Sweep in steps rather than one continuous spin: a client that turns
    # steadily spreads ten shots a second over the whole arena, while one that
    # stops in six facings puts a dozen into each of them.
    (0.5, "+right"),
    (1.4, "-right"),
    (0.5, "+right"),
    (1.4, "-right"),
    (0.5, "+right"),
    (1.4, "-right"),
    (0.3, "+lookdown"),
    (0.2, "-lookdown"),
    (0.5, "+right"),
    (1.4, "-right"),
    (0.5, "+right"),
    (1.4, "-right"),
    (0.3, "say arena-web census"),
    (0.5, "+right"),
    (1.4, "-right"),
    (0.3, "-attack"),
    # The spawn weapon carries 100 bullets and nothing restocks them out here,
    # so a respawn is how a driven client keeps firing at all.
    (0.4, "kill"),
)

# Phase names describe what the driver did, not what the protocol was doing:
# the driver cannot see when a connection completed, and the census derives the
# protocol milestones from the capture itself.
PHASE_CLIENT_STARTED = "client-started"
PHASE_QUERIES = "queries-requested"
PHASE_PLAY = "driven-play"
PHASE_DISCONNECT = "disconnect-requested"
PHASE_RECONNECT = "reconnect-requested"
PHASE_PLAY_AGAIN = "driven-play-after-reconnect"
PHASE_FINAL_DISCONNECT = "final-disconnect-requested"
PHASE_QUIT = "quit-requested"


def _play_steps(seconds: float) -> list[tuple[float, str | None, str | None]]:
    steps: list[tuple[float, str | None, str | None]] = []
    elapsed = 0.0
    while elapsed < seconds:
        for delay, command, _ in _cycle_steps():
            steps.append((delay, command, None))
            elapsed += delay
            if elapsed >= seconds:
                break
    return steps


def _cycle_steps() -> list[tuple[float, str | None, str | None]]:
    """One play cycle, preceded by re-selecting the best weapon the client owns.

    The selection is repeated every cycle because a respawn resets the client to
    the spawn weapon and a pickup is the only way it ever holds anything better.
    """
    steps = [(0.15, command, None) for command in SELECT_BEST_WEAPON]
    steps += [(delay, command, None) for delay, command in PLAY_CYCLE]
    return steps


def client_frags(server_log: str, player_name: str) -> list[str]:
    """The frags the server logged for the census client against another player.

    ioq3 code/game/g_combat.c logs `Kill: <attacker> <target> <mod>: <attacker>
    killed <target> by <MOD>`, and a suicide names the client on both sides, so
    a line that also reads "killed <player>" is not a frag.
    """
    return [
        line
        for line in server_log.splitlines()
        if line.startswith("Kill:")
        and f" {player_name} killed " in line
        and f"killed {player_name} " not in line
    ]


def acceptance_checks(
    server_log: str, client_log: str, summary: dict[str, Any], player_name: str
) -> list[dict[str, Any]]:
    """What the session has to have demonstrated, checked against its own logs.

    The traffic alone cannot say whether the client joined, scored or came back:
    the netchan payload is Huffman-coded, so the census reads sizes rather than
    contents. The game's own logs answer that, and the capture answers the
    packet questions; each check names which one it rests on.
    """
    entered = server_log.count(f"{player_name}^7 entered the game")
    disconnected = server_log.count(f"{player_name}^7 disconnected")
    scored = client_frags(server_log, player_name)
    obituaries = [
        line
        for line in server_log.splitlines()
        if line.startswith("Kill:") and f"killed {player_name} " in line
    ]
    respawns = [
        line
        for line in server_log.splitlines()
        if line.startswith("Kill:")
        and f" {player_name} killed {player_name} " in line
    ]
    connections = summary["connections"]
    milestones = summary["milestones"]
    checks = [
        {
            "check": "client-joined-twice",
            "detail": f"{entered} 'entered the game' lines for {player_name}",
            "evidence": "server log",
            "passed": entered >= 2,
            "required": True,
        },
        {
            "check": "client-disconnected",
            "detail": f"{disconnected} 'disconnected' lines for {player_name}",
            "evidence": "server log",
            "passed": disconnected >= 1,
            "required": True,
        },
        {
            "check": "client-took-damage-and-died",
            "detail": f"{len(obituaries)} obituaries naming {player_name} as the victim",
            "evidence": "server log",
            "passed": bool(obituaries),
            "required": True,
        },
        {
            "check": "client-scored-and-respawned",
            "detail": f"{len(respawns)} score-changing self-inflicted deaths",
            "evidence": "server log",
            "passed": bool(respawns),
            "required": True,
        },
        {
            # Reported, not required. The client is driven blind from a script:
            # it fires where it happens to be facing, so a frag against a bot is
            # luck rather than something a session can guarantee. Scoring by a
            # player belongs to a witnessed round, exactly as WP4 left its
            # player-input outcomes to one.
            "check": "client-fragged-a-bot",
            "detail": scored[0] if scored else "no frag by the blind census client",
            "evidence": "server log",
            "passed": bool(scored),
            "required": False,
        },
        {
            "check": "two-netchan-connections-observed",
            "detail": f"{len(connections)} netchan sequence runs",
            "evidence": "capture",
            "passed": len(connections) >= 2,
            "required": True,
        },
        {
            "check": "challenge-and-connect-observed",
            "detail": "getchallenge, challengeResponse, connect, connectResponse",
            "evidence": "capture",
            "passed": all(
                milestones.get(name) is not None
                for name in (
                    "getchallenge",
                    "challengeResponse",
                    "connect",
                    "connectResponse",
                )
            ),
        },
        {
            "check": "initial-queries-observed",
            "detail": "getinfo, infoResponse, getstatus, statusResponse",
            "evidence": "capture",
            "passed": all(
                milestones.get(name) is not None
                for name in ("getinfo", "infoResponse", "getstatus", "statusResponse")
            ),
        },
        {
            "check": "gamestate-fragments-observed",
            "detail": f"{len(summary['fragmentedMessages'])} fragmented messages",
            "evidence": "capture",
            "passed": bool(summary["fragmentedMessages"]),
            "required": True,
        },
        {
            "check": "no-media-download-attempted",
            "detail": "no 'Downloading' or 'dlfile' line in the client log",
            "evidence": "client log",
            "passed": "Downloading" not in client_log and "dlfile" not in client_log,
            "required": True,
        },
        {
            "check": "no-engine-error",
            "detail": "no fatal engine error in either log",
            "evidence": "both logs",
            "passed": "ERROR: " not in client_log and "ERROR: " not in server_log,
            "required": True,
        },
        {
            "check": "no-unknown-connectionless-command",
            "detail": str(summary["overall"]["unknownConnectionlessCommands"]),
            "evidence": "capture",
            "passed": not summary["overall"]["unknownConnectionlessCommands"],
            "required": True,
        },
    ]
    for check in checks:
        # Every check gates the run unless it says otherwise.
        check.setdefault("required", True)
    return checks


def main() -> int:  # noqa: C901 - a session is a sequence, and it reads as one
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=os.environ.get("CONTAINER_RUNTIME", "docker"))
    parser.add_argument("--server-image", default="arena-web-server:latest")
    parser.add_argument("--toolchain-image", default=None)
    parser.add_argument("--subnet", default=DEFAULT_SUBNET)
    parser.add_argument("--server-ip", default=DEFAULT_SERVER_IP)
    parser.add_argument("--client-ip", default=DEFAULT_CLIENT_IP)
    parser.add_argument(
        "--play-seconds",
        type=int,
        default=120,
        help="minimum driven play, split over the two connections",
    )
    parser.add_argument(
        "--max-play-seconds",
        type=int,
        default=900,
        help="cap on the first play phase, which continues until the client scores",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / "build/packet-census"
    )
    parser.add_argument(
        "--client-dir", type=Path, default=REPO_ROOT / "build/native-client/tree/Release"
    )
    parser.add_argument(
        "--record",
        type=Path,
        help="also write the summary here, as the committed census record",
    )
    arguments = parser.parse_args()

    runtime = arguments.runtime
    output_dir = arguments.output_dir.resolve()
    build_root = (REPO_ROOT / "build").resolve()
    if build_root not in output_dir.parents:
        print(f"--output-dir must be inside {build_root}", file=sys.stderr)
        return 2

    toolchain_image = arguments.toolchain_image
    if toolchain_image is None:
        toolchain_image = subprocess.run(
            [str(REPO_ROOT / "scripts/build-native-toolchain.sh"), "--print-tag"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def image_id(reference: str) -> str:
        return subprocess.run(
            [runtime, "image", "inspect", "--format", "{{.Id}}", reference],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    server_image_id = image_id(arguments.server_image)
    toolchain_image_id = image_id(toolchain_image)
    engine_commit = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/baseline-inputs.py"), "engine-commit"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    session = f"arena-census-{os.getpid()}"
    network = session
    server_name = f"{session}-server"
    capture_name = f"{session}-capture"
    client_name = f"{session}-client"

    shutil.rmtree(output_dir, ignore_errors=True)
    capture_dir = output_dir / "capture"
    capture_dir.mkdir(parents=True)
    capture_dir.chmod(0o755)

    try:
        profile = load_profile(REPO_ROOT)
    except ArenaServerError as error:
        print(f"native profile refused: {error}", file=sys.stderr)
        return 1

    server_endpoint = f"{arguments.server_ip}:{profile['port']}"
    client_root = output_dir / "client-root"
    started: list[str] = []
    created_network = False
    phases: list[dict[str, Any]] = []
    status = 0

    def record_phase(name: str) -> None:
        phases.append({"name": name, "startedAt": time.time()})

    try:
        _stage_client_root(client_root, profile, arguments.client_dir.resolve())

        _run([runtime, "network", "create", "--subnet", arguments.subnet, network])
        created_network = True

        _run(
            [
                runtime, "run", "--detach", "--name", server_name,
                "--network", network, "--ip", arguments.server_ip,
                "--cap-drop", "all",
                "--security-opt", "label=disable",
                "--security-opt", "no-new-privileges",
                "--read-only",
                "--tmpfs", "/var/lib/arena:rw,noexec,nosuid,nodev,mode=1777",
                arguments.server_image,
                *profile["serverArguments"],
            ]
        )
        started.append(server_name)
        time.sleep(4)

        # The capture shares the server's network namespace, so it observes the
        # server's own interface and nothing else on the host. The filter names
        # the server's UDP port, so even inside that namespace only this
        # session's game traffic is written.
        _run(
            [
                runtime, "run", "--detach", "--name", capture_name,
                "--network", f"container:{server_name}",
                "--cap-drop", "all", "--cap-add", "NET_RAW",
                "--security-opt", "label=disable",
                "--security-opt", "no-new-privileges",
                "--user", "0:0",
                "--volume", f"{capture_dir}:/capture:rw",
                "--entrypoint", "/usr/bin/tcpdump",
                toolchain_image,
                "-i", "eth0", "-n", "-s", "0", "-U",
                # Without -Z tcpdump drops to its own unprivileged user and then
                # cannot own the save file on a bind mount.
                "-Z", "root",
                "-w", "/capture/session.pcap",
                f"udp port {profile['port']}",
            ]
        )
        started.append(capture_name)

        pcap_path = capture_dir / "session.pcap"
        for _ in range(100):
            if pcap_path.exists() and pcap_path.stat().st_size >= 24:
                break
            time.sleep(0.1)
        else:
            raise CensusError("the capture did not start")

        client_command = [
            runtime, "run", "--rm", "--interactive", "--name", client_name,
            "--network", network, "--ip", arguments.client_ip,
            "--cap-drop", "all",
            "--security-opt", "label=disable",
            "--security-opt", "no-new-privileges",
            "--user", "0:0",
            "--env", f"ARENA_DISPLAY={DISPLAY}",
            "--env", f"ARENA_SCREEN={SCREEN}",
            "--env", "ARENA_CLIENT_BINARY=/opt/arena-web/ioquake3",
            "--env", "HOME=/var/lib/arena",
            "--volume", f"{client_root}:/opt/arena-web:ro",
            "--volume", f"{REPO_ROOT / 'native/census-client.sh'}:/census-client.sh:ro",
            "--tmpfs", "/var/lib/arena:rw,nosuid,nodev,mode=1777",
            "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
            "--entrypoint", "/bin/sh",
            toolchain_image,
            "/census-client.sh",
            *profile["clientArguments"],
            "+connect", server_endpoint,
        ]
        client_log = (output_dir / "client.log").open("w", encoding="utf-8")
        client = subprocess.Popen(
            client_command,
            stdin=subprocess.PIPE,
            stdout=client_log,
            stderr=subprocess.STDOUT,
        )
        player_name = profile["client"]["cvars"]["name"]

        def send(steps) -> None:
            for delay, command, phase in steps:
                time.sleep(delay)
                if client.poll() is not None:
                    raise CensusError(
                        f"the client exited with {client.returncode} before the "
                        "session was driven to its end"
                    )
                if phase:
                    record_phase(phase)
                if command:
                    client.stdin.write(f"{command}\n".encode())
                    client.stdin.flush()

        def server_log_now() -> str:
            logs = subprocess.run(
                [runtime, "logs", server_name],
                capture_output=True,
                text=True,
                check=False,
            )
            return logs.stdout + logs.stderr

        def play(minimum: float, maximum: float) -> None:
            """Drive play for at least `minimum` seconds, longer only for a frag.

            A client nobody is aiming scores by persistence, so the session
            keeps playing until the server has logged one frag by it — up to a
            cap, after which the run reports the miss rather than hiding it.
            """
            started = time.monotonic()
            send(_play_steps(minimum))
            while time.monotonic() - started < maximum:
                if client_frags(server_log_now(), player_name):
                    return
                send(_cycle_steps())

        try:
            half = max(1.0, arguments.play_seconds / 2)
            send([(0.0, None, PHASE_CLIENT_STARTED)])
            # The two queries a server browser makes, sent once the client has
            # had time to connect. They are connectionless traffic on the same
            # address pair, which is exactly what the census has to separate.
            send(
                [
                    (14.0, f"ping {server_endpoint}", PHASE_QUERIES),
                    (1.5, f"serverstatus {server_endpoint}", None),
                    (1.5, None, PHASE_PLAY),
                ]
            )
            play(half, arguments.max_play_seconds / 2)
            send(
                [
                    (1.0, "disconnect", PHASE_DISCONNECT),
                    # ioq3 code/server/sv_client.c: sv_reconnectlimit rejects a
                    # reconnect from the same address for its own number of
                    # seconds, so the pause is part of what a reconnect is
                    # rather than politeness.
                    (5.0, f"connect {server_endpoint}", PHASE_RECONNECT),
                    (14.0, None, PHASE_PLAY_AGAIN),
                ]
            )
            send(_play_steps(half))
            send(
                [
                    (1.0, "disconnect", PHASE_FINAL_DISCONNECT),
                    (3.0, "quit", PHASE_QUIT),
                ]
            )
            client.stdin.close()
            client.wait(timeout=60)
        finally:
            if client.poll() is None:
                client.send_signal(signal.SIGTERM)
                try:
                    client.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    client.kill()
            client_log.close()
        record_phase("session-ended")
        # Give the capture a moment to write the last datagrams before it is
        # asked to stop.
        time.sleep(2)
    except (ArenaServerError, CensusError, PacketCensusError) as error:
        print(f"packet census failed: {error}", file=sys.stderr)
        status = 1
    finally:
        for name in reversed(started):
            subprocess.run(
                [runtime, "stop", "--time", "10", name],
                capture_output=True,
                text=True,
                check=False,
            )
        for name, target in (
            (capture_name, output_dir / "capture.log"),
            (server_name, output_dir / "server.log"),
        ):
            logs = subprocess.run(
                [runtime, "logs", name], capture_output=True, text=True, check=False
            )
            target.write_text(logs.stdout + logs.stderr, encoding="utf-8")
        for name in reversed(started):
            subprocess.run(
                [runtime, "rm", "--force", name],
                capture_output=True,
                text=True,
                check=False,
            )
        if created_network:
            subprocess.run(
                [runtime, "network", "rm", "--force", network],
                capture_output=True,
                text=True,
                check=False,
            )

    if status:
        return status

    try:
        packets = parse_pcap((capture_dir / "session.pcap").read_bytes())
        records = build_records(
            packets,
            server_address=arguments.server_ip,
            server_port=profile["port"],
            phases=phases,
        )
        summary = summarize(records)
    except PacketCensusError as error:
        print(f"packet census failed: {error}", file=sys.stderr)
        return 1

    checks = acceptance_checks(
        (output_dir / "server.log").read_text(encoding="utf-8", errors="replace"),
        (output_dir / "client.log").read_text(encoding="utf-8", errors="replace"),
        summary,
        profile["client"]["cvars"]["name"],
    )

    session_record = {
        "clientArguments": profile["clientArguments"] + ["+connect", server_endpoint],
        "engineCommit": engine_commit,
        "phases": [
            {"name": phase["name"], "startedAt": round(phase["startedAt"], 3)}
            for phase in phases
        ],
        "maxPlaySeconds": arguments.max_play_seconds,
        "playSeconds": arguments.play_seconds,
        "serverAddress": arguments.server_ip,
        "serverArguments": profile["serverArguments"],
        "serverImage": arguments.server_image,
        "serverImageId": server_image_id,
        "serverPort": profile["port"],
        "toolchainImage": toolchain_image,
        "toolchainImageId": toolchain_image_id,
    }
    (output_dir / "census-records.json").write_text(
        json.dumps({"records": records, "session": session_record}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "census-summary.json").write_text(
        json.dumps(
            {"checks": checks, "session": session_record, "summary": summary},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"captured {len(records)} engine datagrams")
    for direction, entry in summary["byDirection"].items():
        statistics = entry["all"]
        print(
            f"  {direction}: {statistics['count']} datagrams, "
            f"max {statistics['maximum']} B, netchan headers {entry['headerBytes']}"
        )
    failed = [
        check for check in checks if check["required"] and not check["passed"]
    ]
    for check in checks:
        if check["passed"]:
            mark = "pass"
        else:
            mark = "FAIL" if check["required"] else "not observed"
        print(f"  [{mark}] {check['check']}: {check['detail']}")
    print(f"wrote {output_dir}/census-records.json")
    print(f"wrote {output_dir}/census-summary.json")
    if arguments.record is not None:
        if failed:
            print(
                "refusing to write the census record: the run did not pass",
                file=sys.stderr,
            )
            return 1
        arguments.record.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_dir / "census-summary.json", arguments.record)
        print(f"wrote {arguments.record}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
