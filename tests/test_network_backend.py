# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
CC = shutil.which("cc") or shutil.which("gcc")


class EngineBoundaryTests(unittest.TestCase):
    def test_committed_boundary_constants_are_identical(self) -> None:
        profile = json.loads((ROOT / "arena/relay-profile.json").read_text())
        network = (ROOT / "ioq3/code/qcommon/net_ip.c").read_text(encoding="utf-8")
        backend = (ROOT / "arena/network-backend.js").read_text(encoding="utf-8")
        net_chan = (ROOT / "ioq3/code/qcommon/net_chan.c").read_text(encoding="utf-8")

        c_floor = int(re.search(r"#define ARENA_INNER_DATAGRAM_FLOOR (\d+)", network)[1])
        js_floor = int(re.search(r"export const INNER_DATAGRAM_FLOOR = (\d+)", backend)[1])
        fragment = int(re.search(r"#define\s+FRAGMENT_SIZE\s+(\d+)", net_chan)[1])
        self.assertEqual({c_floor, js_floor, profile["innerDatagramFloor"]}, {768})
        self.assertEqual(fragment, profile["fragmentSize"])

    def test_protocol_constant_and_native_boundary_harness(self) -> None:
        if CC is None:
            self.skipTest("a C compiler is not available")
        source = (ROOT / "ioq3/code/qcommon/net_chan.c").read_text(encoding="utf-8")
        match = re.search(r"^#define\s+FRAGMENT_SIZE\s+(\d+)\s*$", source, re.MULTILINE)
        self.assertIsNotNone(match)
        fragment_size = int(match.group(1))
        self.assertEqual(fragment_size, 704)
        self.assertRegex(source, r"#define\s+MAX_PACKETLEN\s+1400\b")

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "wp7-engine-harness"
            command = [
                CC,
                "-std=gnu99",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-error=sign-compare",
                "-Wno-error=unused-parameter",
                "-ffunction-sections",
                "-fdata-sections",
                f"-I{ROOT / 'ioq3/code/qcommon'}",
                str(ROOT / "tests/wp7_engine_harness.c"),
                str(ROOT / "ioq3/code/qcommon/net_chan.c"),
                str(ROOT / "ioq3/code/qcommon/msg.c"),
                str(ROOT / "ioq3/code/qcommon/q_shared.c"),
                str(ROOT / "ioq3/code/qcommon/huffman.c"),
                "-Wl,--gc-sections",
                "-o",
                str(executable),
            ]
            built = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("passed", ran.stdout)

    def test_engine_boundary_is_below_compression_and_delay_queue(self) -> None:
        net_chan = (ROOT / "ioq3/code/qcommon/net_chan.c").read_text(encoding="utf-8")
        self.assertIn("Huff_Compress( &mbuf, 12);", net_chan)
        self.assertIn("Sys_SendPacket(packetQueue->sock", net_chan)
        self.assertIn("Sys_SendPacket( sock, packetClass", net_chan)
        self.assertIn("NET_OutOfBandPrintElicited", net_chan)

        client = (ROOT / "ioq3/code/client/cl_main.c").read_text(encoding="utf-8")
        cap = client.index('strlen( data ) > 512')
        compressed_send = client.index("NET_OutOfBandData", cap)
        self.assertLess(cap, compressed_send)
        self.assertIn("Com_Error( ERR_DROP", client[cap:compressed_send])

        network = (ROOT / "ioq3/code/qcommon/net_ip.c").read_text(encoding="utf-8")
        boundary = network.index("void Sys_SendPacket")
        self.assertIn("length > ARENA_INNER_DATAGRAM_FLOOR", network[boundary:])
        self.assertIn("ArenaWeb_SendPacket", network[boundary:])
        self.assertIn("(int)(unsigned short)BigShort( to.port )", network[boundary:])

    def test_real_dedicated_sys_send_boundary(self) -> None:
        if CC is None:
            self.skipTest("a C compiler is not available")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "wp7-sys-send-harness"
            command = [
                CC,
                "-std=gnu99",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-error=sign-compare",
                "-Wno-error=unused-parameter",
                "-ffunction-sections",
                "-fdata-sections",
                "-DDEDICATED",
                f"-I{ROOT / 'ioq3/code/qcommon'}",
                str(ROOT / "tests/wp7_sys_send_harness.c"),
                str(ROOT / "ioq3/code/qcommon/net_ip.c"),
                str(ROOT / "ioq3/code/qcommon/q_shared.c"),
                "-Wl,--gc-sections",
                "-o",
                str(executable),
            ]
            built = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("passed", ran.stdout)

    def test_real_rate_limit_bucket_keying(self) -> None:
        if CC is None:
            self.skipTest("a C compiler is not available")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "wp7-rate-limit-harness"
            command = [
                CC,
                "-std=gnu99",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-error=sign-compare",
                "-Wno-error=unused-parameter",
                "-ffunction-sections",
                "-fdata-sections",
                f"-I{ROOT / 'ioq3/code/qcommon'}",
                f"-I{ROOT / 'ioq3/code/server'}",
                str(ROOT / "tests/wp7_rate_limit_harness.c"),
                "-Wl,--gc-sections",
                "-o",
                str(executable),
            ]
            built = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("passed", ran.stdout)

    def test_browser_randomness_uses_the_web_crypto_csprng(self) -> None:
        source = (ROOT / "ioq3/code/sys/sys_unix.c").read_text(encoding="utf-8")
        common = (ROOT / "ioq3/code/qcommon/common.c").read_text(encoding="utf-8")
        self.assertIn("cryptoProvider.getRandomValues", source)
        self.assertIn("offset += 65536", source)
        self.assertNotIn("Math.random", source)
        browser_failure = common[common.index("void Com_RandomBytes") :]
        self.assertIn("#ifdef __EMSCRIPTEN__", browser_failure)
        self.assertIn('Com_Error( ERR_FATAL', browser_failure)
        self.assertLess(
            browser_failure.index('Com_Error( ERR_FATAL'),
            browser_failure.index('using weak randomization'),
        )

        init_rand_start = common.index("static void Com_InitRand")
        init_rand_end = common.index("/*", init_rand_start + 1)
        init_rand = common[init_rand_start:init_rand_end]
        self.assertIn("#ifdef __EMSCRIPTEN__", init_rand)
        self.assertIn("Com_Error(ERR_FATAL", init_rand)
        self.assertLess(
            init_rand.index("Com_Error(ERR_FATAL"), init_rand.index("srand(time(NULL))")
        )

    def test_browser_host_stop_uses_the_normal_engine_quit_path(self) -> None:
        source = (ROOT / "ioq3/code/sys/sys_main.c").read_text(encoding="utf-8")
        exported = source.index("EMSCRIPTEN_KEEPALIVE void Web_RequestQuit")
        loop = source.index("static void Sys_WebMainLoop", exported)
        consumed = source.index("Com_Quit_f();", loop)
        frame = source.index("Com_Frame();", consumed)
        installed = source.index("emscripten_set_main_loop( Sys_WebMainLoop")
        self.assertLess(exported, loop)
        self.assertLess(loop, consumed)
        self.assertLess(consumed, frame)
        self.assertLess(frame, installed)


@unittest.skipUnless(NODE, "node is not available to run the browser backend")
class BrowserBackendTests(unittest.TestCase):
    def test_deterministic_backend_harness(self) -> None:
        result = subprocess.run(
            [NODE, str(ROOT / "tests/network_backend_harness.mjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(report["checks"], 29)

    def test_browser_only_commands_are_structurally_neutralized(self) -> None:
        client = (ROOT / "ioq3/code/client/cl_main.c").read_text(encoding="utf-8")
        browser_block = client[client.index("#ifdef __EMSCRIPTEN__", client.index('Cmd_AddCommand ("connect"')) :]
        for command in ("localservers", "globalservers", "rcon", "ping", "serverstatus"):
            self.assertIn(f'Cmd_AddCommand ("{command}", CL_RelayBrowserCommand_f', browser_block)

    def test_browser_receive_refusals_are_counted_before_polling_continues(self) -> None:
        network = (ROOT / "ioq3/code/qcommon/net_ip.c").read_text(encoding="utf-8")
        receive = network[network.index("EM_JS(int, ArenaWeb_ReceivePacket") :]
        self.assertIn('refuseReceivedForEngine?.("invalid_payload")', receive)
        self.assertIn('refuseReceivedForEngine?.("engine_capacity")', receive)
        self.assertIn("continue;", receive)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
