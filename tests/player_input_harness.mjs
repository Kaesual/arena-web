// SPDX-License-Identifier: GPL-2.0-or-later
//
// Runs the loader's player-name and player-model rules under Node so that
// tests/test_arena_runtime.py can compare them with the Python ones. The two
// implementations exist because neither file can import the other; this is what
// stops them from drifting apart, which is the same arrangement the engine
// command-line budget already has.
//
// A test harness, not part of the loader. It touches no network, reads only the
// player-input module and the committed relay profile, and writes only to
// stdout.

import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [repoRoot] = process.argv.slice(2);
const module_ = await import(
  pathToFileURL(path.join(repoRoot, "arena", "player-input.js")).href
);
const profile = JSON.parse(
  fs.readFileSync(path.join(repoRoot, "arena", "relay-profile.json"), "utf8"),
);
const cases = JSON.parse(fs.readFileSync(0, "utf8"));

const results = cases.map((value) => {
  try {
    return {
      accepted: true,
      name: module_.playerName(value, profile.playerSettings.name),
    };
  } catch (error) {
    return { accepted: false, error: String(error && error.message) };
  }
});
process.stdout.write(JSON.stringify(results));
