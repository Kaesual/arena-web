// SPDX-License-Identifier: GPL-2.0-or-later
//
// The player's own two runtime inputs — the name they are called and the model
// they wear — turned into engine arguments.
//
// **Its own module rather than part of the loader, and that is the point.**
// `scripts/arena_runtime.py` carries the same rule, because neither file can
// import the other, and two implementations of one rule drift unless something
// runs both. This one is importable under Node, so
// `tests/test_arena_runtime.py` compares them over a shared table — the same
// arrangement the engine command-line budget already has.

// Printable ASCII 0x20-0x7E less `"` 0x22, `;` 0x3B, `\` 0x5C and `^` 0x5E,
// and no repeated space. **Deliberately narrower than what `ClientCleanName`
// accepts rather than a model of it**: every accepted name is one that function
// stores verbatim (ioq3 code/game/g_client.c), so what a player types is what
// the scoreboard shows.
export const PLAYER_NAME = /^(?!.*  )[ !#-:<-[\]_-~]+$/;

// The three cvars the two choices become. `headmodel` follows `model` because
// ioq3 registers it separately with its own `sarge` default (code/client/
// cl_main.c), so setting one without the other would give the player a packaged
// body and an unpackaged head — and `sarge` is not in this pack, which makes
// that a `CG_Error` rather than a cosmetic mismatch.
export const PLAYER_SETTING_CVARS = ["headmodel", "model", "name"];

function requireString(value, what) {
  if (typeof value !== "string" || value === "") {
    throw new Error(`${what} must be a non-empty string`);
  }
  return value;
}

/**
 * One player's name, as the engine will store it.
 *
 * **Three failures, three answers, and the split is the decision.** Length is
 * *truncated*: a session must not fail over a name that is merely long, and
 * truncating here rather than leaving it to `ClientCleanName` — whose
 * `outpos < outSize - 1` loop drops the rest without a word — is what makes the
 * stored name predictable instead of merely bounded. Outer whitespace is
 * *trimmed*: it is invisible, `ClientCleanName` discards leading spaces itself,
 * and a cut can otherwise expose a trailing one. Content is *refused*, because
 * it is the only one of the three a player can see and report.
 *
 * `^` is the one worth arguing, and it is refused. `ClientCleanName` reads `^`
 * plus a character as a colour code, drops it outright when the colour is
 * black, and falls back to "UnnamedPlayer" when nothing colourless survives —
 * so accepting it would mean a name that silently becomes colour, or silently
 * becomes the very default this setting exists to replace. Neither is one the
 * player who typed it could diagnose.
 *
 * The order is not arbitrary: outer spaces come off first so the content rule
 * is not reporting an invisible defect, then content is checked so a refusal
 * names something visible, then the value is cut and trimmed again.
 */
export function playerName(value, bound, what = "playerName") {
  const stripped = requireString(value, what).replace(/^ +| +$/g, "");
  if (stripped.length < bound.minLength) {
    throw new Error(
      `${what} is empty or only spaces, and the engine would answer that with ` +
        '"UnnamedPlayer" — the default this setting exists to replace',
    );
  }
  if (!PLAYER_NAME.test(stripped)) {
    throw new Error(
      `${what} must be printable ASCII without '"', ';', '\\' or '^', or a ` +
        "repeated space; every accepted name is one ClientCleanName stores unchanged",
    );
  }
  return stripped.slice(0, bound.maxLength).replace(/ +$/, "");
}

/** One player-chosen model, from the set this release actually packages. */
export function playerModel(value, offered, what = "playerModel") {
  const model = requireString(value, what);
  if (!offered.includes(model)) {
    throw new Error(
      `${what} '${model}' is not a model this release packages; it offers ` +
        offered.join(", "),
    );
  }
  return model;
}

/**
 * The player's half of the relay client's command line.
 *
 * The bounds come from the committed relay profile rather than from here, and
 * `scripts/arena_runtime.py` checks each of those bounds against the thing that
 * derives it — the model list against the packaged set, the length against the
 * pinned `MAX_NETNAME`.
 */
export function playerLaunchArguments(profile, configuration) {
  const settings = profile.playerSettings;
  const model = playerModel(configuration.playerModel, settings.models);
  const name = playerName(configuration.playerName, settings.name);
  return ["+set", "headmodel", model, "+set", "model", model, "+set", "name", name];
}
