// SPDX-License-Identifier: GPL-2.0-or-later
//
// The product-owned browser loader of the one-map FFA slice. It retains the
// offline WP4 profile and can be configured at runtime for the WP7 relay path.
//
// This is original arena-web code. ioquake3's generated Emscripten shell is
// build evidence only (docs/wp1-build-evidence.md) and is neither packaged nor
// copied here; the Module surface used below — canvas, arguments, locateFile,
// instantiateWasm, preRun, print/printErr — is the public Emscripten contract
// the pinned build emits.
//
// What it does, in order:
//
//   1. reads the committed content configuration (game-profile.json) and the
//      two committed manifests it names;
//   2. fetches every declared artifact from this origin and refuses to
//      continue unless its SHA-256 and byte length equal the committed
//      identity — the engine module and the WebAssembly are then executed from
//      exactly those verified bytes, not from a second fetch;
//   3. waits for a real user gesture, so every audio context the engine
//      creates is created under user activation;
//   4. boots either the offline map-with-bots profile or the configured relay
//      client and records load timing, frame timing, safe network counters, the
//      engine's own console output and the runtime identities.
//
// It implements only the loader behaviour the work package names: canvas
// sizing, pointer lock, keyboard/mouse input, fullscreen and user-activated
// audio. There is no settings persistence, OPFS or account integration.

import {
  measureCanvas,
  observeCanvasResize,
  renderSizeArguments as canvasRenderSizeArguments,
} from "./arena/canvas-resize.js";
import { createHostLifecycle } from "./arena/host-lifecycle.js";
import { playerLaunchArguments } from "./arena/player-input.js";
import {
  ArenaNetworkSession,
  INNER_DATAGRAM_FLOOR,
  PathBudgetError,
  RECEIVE_QUEUE_DEPTH,
} from "./arena/network-backend.js";

const PROFILE_URL = "game-profile.json";
const RELAY_PROFILE_URL = "arena/relay-profile.json";

// Which maps this page is being opened for. The committed profile declares
// *every* published archive and this loader verifies each one it fetches
// against the committed manifest, so the choice below is a selection from an
// already trusted set and not a new trust decision — but it is what decides
// how many megabytes a player waits for, so it has to be made rather than
// assumed. It is required: see selectArtifacts.
const ROTATION_PARAMETER = "maps";

// The one placeholder a ready marker may carry, filled in with the map the
// rotation actually starts. scripts/arena_runtime.py writes the same constant.
const MARKER_MAP_PLACEHOLDER = "{map}";

// The engine's own verdict on a rotation that was too small. With
// `cl_allowDownload` clear — which both profiles guarantee — `CL_InitDownloads`
// compares the server's referenced paks against what this client mounted and
// prints this line with the missing names (ioq3 code/client/cl_main.c). It
// arrives at the map change that breaks, so it prevents nothing; what it does
// is name the archive, inside the component that fails, instead of leaving an
// operator to reconstruct why a client dropped.
const MISSING_PAK_MARKER = "You are missing some files referenced by the server";
const ENGINE_LOG_LIMIT = 40000;
// How long stop() waits for the engine's own quit to settle before it closes
// the relay itself. The request is consumed on the next engine frame and the
// shutdown that follows is synchronous, so this is far more than the path
// needs; it exists so that an engine which never gets there cannot leave the
// session open, not as a schedule anything depends on.
const ENGINE_QUIT_GRACE_MILLISECONDS = 2000;
const FRAME_SAMPLE_LIMIT = 30000;
const EVENT_LIMIT = 10000;
const LONG_FRAME_MS = 50;

const ARTIFACT_ROLES = new Set(["module-script", "module-wasm", "filesystem"]);

class LoaderError extends Error {
  constructor(message) {
    super(message);
    this.name = "LoaderError";
  }
}

const elements = {
  stage: document.getElementById("stage"),
  canvas: document.getElementById("canvas"),
  overlay: document.getElementById("overlay"),
  message: document.getElementById("message"),
  progressBar: document.getElementById("progress-bar"),
  start: document.getElementById("start"),
  fullscreen: document.getElementById("fullscreen"),
  hint: document.getElementById("hint"),
};

// Everything an evidence run needs to read out of a finished page. The
// acceptance driver reads exactly this object; nothing here is used to steer
// the engine.
const report = {
  status: "starting",
  error: null,
  profile: null,
  identities: [],
  configFiles: [],
  botEntries: [],
  totalArtifactBytes: 0,
  timings: { pageStartedAtEpochMs: Date.now() },
  markers: {},
  engineArguments: [],
  engineLog: [],
  engineLogDropped: 0,
  browserErrors: [],
  frames: { samples: 0, dropped: 0 },
  events: [],
  eventsDropped: 0,
  // What was asked for, what it resolved to, what was fetched — and, if the
  // server ever says so, what was missing anyway.
  // The subset rule this serves is an integration obligation (see
  // docs/integration-contract.md) that nothing downstream can catch: a client
  // missing one map connects and plays normally and is dropped hours later,
  // when rotation reaches it. This is the record that turns that post-mortem
  // into one step.
  rotation: null,
  unexpectedFileRequests: [],
  audioActivation: null,
  // `unadjusted` is null until a lock is asked for, then true or false: the
  // request below asks for raw pointer deltas and accepts the ordinary ones,
  // so which of the two a session actually got is a fact about that session
  // rather than about this build. It stays null on a browser that ignores the
  // option without saying so, which is the honest answer there.
  pointerLock: {
    supported: "pointerLockElement" in document,
    engaged: false,
    errors: 0,
    unadjusted: null,
  },
  fullscreen: { supported: Boolean(elements.stage.requestFullscreen), engaged: false },
  progress: { phase: "loading", loadedBytes: 0, totalBytes: null, fraction: 0 },
  exit: null,
  webglContextLost: 0,
  render: null,
  mode: "offline",
  relay: null,
};

let relayRuntimeConfiguration = null;
let relayProfile = null;
let relayBackend = null;
let engineStarted = false;
let relayReconnectReady = false;
let relayReconnectRunning = false;
let canvasResizeBridge = null;
let loadedProfile = null;
let loadedArtifacts = null;
let loadedSelection = null;
// The canonical fetch set, for the record the snapshot carries.
let loadedRotation = null;
// The map the offline slice starts: the rotation's first entry as the caller
// wrote it. Null on the relay path, which starts no map of its own.
let loadedStartMap = null;
let startAccepted = false;
let startOperation = null;
let stopRequested = false;
let stopOperation = null;
let engineBootStarted = false;
let engineQuit = null;
let engineQuitInvoked = false;
const startupAbort = new AbortController();
const lifecycle = createHostLifecycle(snapshot, {
  onListenerError: (error) => {
    console.error("arena-web subscriber failed", error);
  },
});

const frameDeltas = [];
const startedAt = performance.now();

function since() {
  return Math.round((performance.now() - startedAt) * 1000) / 1000;
}

function note(kind, detail) {
  if (report.events.length < EVENT_LIMIT) {
    report.events.push({ at: since(), kind, detail: detail ?? null });
  } else {
    report.eventsDropped += 1;
  }
}

function setMessage(text) {
  elements.message.textContent = text;
}

function setProgress(fraction, loadedBytes = null, totalBytes = null, phase = "loading") {
  const clamped = Math.max(0, Math.min(1, fraction));
  elements.progressBar.style.width = `${(clamped * 100).toFixed(1)}%`;
  report.progress = {
    phase,
    loadedBytes,
    totalBytes,
    fraction: Math.round(clamped * 1000000) / 1000000,
  };
  lifecycle.publish();
}

function safeError(error) {
  let message = String(error?.message ?? error ?? "unknown failure");
  const authorization = relayRuntimeConfiguration?.authorization;
  if (typeof authorization === "string" && authorization !== "") {
    message = message.replaceAll(authorization, "[redacted]");
  }
  return {
    name: String(error?.name ?? "Error").slice(0, 80),
    message: message.slice(0, 240),
  };
}

function setStatus(status, error = null) {
  report.status = status;
  report.error = error;
  lifecycle.publish();
}

function settle(status, exitCode, reason) {
  return lifecycle.settle({ status, exitCode, reason }, () => {
    report.exit = { code: exitCode, reason };
    report.status = status;
    report.error = status === "failed" ? report.error : null;
  });
}

function fail(error, reason = "loader_error") {
  if (lifecycle.terminal() !== null || (stopRequested && error?.name === "AbortError")) {
    note("late-failure-ignored", String(error?.name ?? "Error"));
    return;
  }
  report.error = safeError(error);
  elements.overlay.hidden = false;
  elements.start.disabled = true;
  setMessage(`Failed: ${report.error.message}`);
  settle("failed", null, reason);
  // The browser console is part of the acceptance evidence, so a loader
  // failure has to be visible there and not only inside this page.
  console.error("arena-web loader failed", error);
}

function offerRelayReconnect(reason) {
  if (reason === "path_budget") {
    fail(new PathBudgetError());
    return;
  }
  if (!engineStarted || ["failed", "exited"].includes(report.status)) {
    return;
  }
  relayReconnectReady = true;
  setStatus("reconnect-ready", {
    name: "RelayClosedError",
    message: "the relay session ended",
  });
  elements.overlay.hidden = false;
  elements.start.textContent = "Reconnect";
  elements.start.disabled = false;
  setMessage("The relay session ended. Reconnect uses a fresh one-time authorization.");
}

async function reconnectRelay() {
  if (!relayReconnectReady || relayReconnectRunning || relayBackend === null) {
    return;
  }
  relayReconnectReady = false;
  relayReconnectRunning = true;
  elements.start.disabled = true;
  elements.overlay.hidden = true;
  setStatus("reconnecting");
  note("relay-reconnect-started", null);
  try {
    await relayBackend.reconnect();
    report.relay = relayBackend.snapshot();
    setStatus(report.markers.clientGameLoaded === undefined ? "booting" : "running");
    elements.start.textContent = "Start";
    note("relay-reconnect-completed", null);
  } catch (error) {
    if (error instanceof PathBudgetError || error?.retry === false) {
      fail(error);
    } else {
      offerRelayReconnect("attempt_failed");
    }
  } finally {
    relayReconnectRunning = false;
  }
}

function requireObject(value, what) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new LoaderError(`${what}: expected an object`);
  }
  return value;
}

function requireString(value, what) {
  if (typeof value !== "string" || value === "") {
    throw new LoaderError(`${what}: expected a non-empty string`);
  }
  return value;
}

// A served path may only address a file below this page. Anything absolute,
// scheme-bearing or containing a traversal segment is refused before it is
// fetched, so a mis-edited profile cannot make the loader read another origin.
function resolveServed(path, what) {
  requireString(path, what);
  if (path.startsWith("/") || path.includes("..") || path.includes("\\") || /^[a-z][a-z0-9+.-]*:/i.test(path)) {
    throw new LoaderError(`${what}: '${path}' is not a relative path below the loader`);
  }
  const url = new URL(path, document.baseURI);
  const base = new URL(".", document.baseURI);
  if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname)) {
    throw new LoaderError(`${what}: '${path}' escapes the loader directory`);
  }
  return url;
}

async function fetchJson(url, what) {
  const response = await fetch(url, { signal: startupAbort.signal });
  if (!response.ok) {
    throw new LoaderError(`${what}: HTTP ${response.status}`);
  }
  return response.json();
}

function toHex(buffer) {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < bytes.length; i += 1) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

async function sha256Hex(bytes) {
  if (!globalThis.crypto?.subtle) {
    throw new LoaderError(
      "crypto.subtle is unavailable, so artifact identities cannot be verified; " +
        "serve the loader from a secure context such as http://127.0.0.1",
    );
  }
  return toHex(await crypto.subtle.digest("SHA-256", bytes));
}

async function fetchBytes(url, what, onProgress) {
  const response = await fetch(url, { signal: startupAbort.signal });
  if (!response.ok) {
    throw new LoaderError(`${what}: HTTP ${response.status}`);
  }
  if (!response.body) {
    const buffer = new Uint8Array(await response.arrayBuffer());
    onProgress(buffer.length, buffer.length);
    return buffer;
  }
  const declared = Number(response.headers.get("content-length") ?? 0);
  const reader = response.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    chunks.push(value);
    received += value.length;
    onProgress(received, declared);
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

function manifestIndex(manifest, what) {
  requireObject(manifest, what);
  if (manifest.digestAlgorithm !== "sha256") {
    throw new LoaderError(`${what}: digestAlgorithm is not 'sha256'`);
  }
  if (!Array.isArray(manifest.artifacts)) {
    throw new LoaderError(`${what}: has no artifact list`);
  }
  const index = new Map();
  for (const entry of manifest.artifacts) {
    requireObject(entry, `${what}: artifact`);
    index.set(requireString(entry.path, `${what}: artifact path`), {
      sha256: requireString(entry.sha256, `${what}: ${entry.path} sha256`),
      size: entry.size,
      // The content manifest's per-archive records. `map` is the selection key
      // the rotation is expressed in, so it has to survive this index or every
      // archive would look like the base; the sizes are carried for a host
      // reading the snapshot rather than used here.
      map: entry.map,
      uncompressedSize: entry.uncompressedSize,
      peakHunkBytes: entry.peakHunkBytes,
    });
  }
  return index;
}

function parseProfile(profile) {
  requireObject(profile, PROFILE_URL);
  if (profile.formatVersion !== 1) {
    throw new LoaderError(`${PROFILE_URL}: unsupported formatVersion`);
  }
  requireString(profile.package, `${PROFILE_URL}: package`);
  // The two silent ceilings the assembled engine command line runs into. They
  // are committed rather than measured here because a page cannot read the
  // pinned engine tree; scripts/arena_runtime.py refuses this profile unless
  // both equal what that tree defines, so what is enforced below is the
  // engine's own bound and not a number somebody typed.
  requireObject(profile.engineCommandLine, `${PROFILE_URL}: engineCommandLine`);
  for (const name of ["maxBytes", "maxLines"]) {
    const value = profile.engineCommandLine[name];
    if (!Number.isInteger(value) || value <= 0) {
      throw new LoaderError(`${PROFILE_URL}: engineCommandLine.${name} is not a positive integer`);
    }
  }
  requireObject(profile.manifests, `${PROFILE_URL}: manifests`);
  requireObject(profile.readyMarkers, `${PROFILE_URL}: readyMarkers`);
  // An empty marker string would be found in every console line the engine
  // prints, so every milestone would fire on the first one.
  for (const [name, needle] of Object.entries(profile.readyMarkers)) {
    requireString(needle, `${PROFILE_URL}: readyMarkers.${name}`);
  }
  if (!Array.isArray(profile.bots) || profile.bots.length === 0) {
    throw new LoaderError(`${PROFILE_URL}: bots must be a non-empty list`);
  }
  for (const bot of profile.bots) {
    requireString(requireObject(bot, `${PROFILE_URL}: bots entry`).name, `${PROFILE_URL}: bots[].name`);
  }
  if (!Array.isArray(profile.artifacts) || profile.artifacts.length === 0) {
    throw new LoaderError(`${PROFILE_URL}: artifacts must be a non-empty list`);
  }
  // The engine's own configuration file. It is product-owned rather than part
  // of the audited content pack, because the pack is the closure of what the
  // game modules reference and this is what the engine itself requires.
  if (!Array.isArray(profile.configFiles) || profile.configFiles.length === 0) {
    throw new LoaderError(`${PROFILE_URL}: configFiles must be a non-empty list`);
  }
  for (const entry of profile.configFiles) {
    requireObject(entry, `${PROFILE_URL}: configFiles entry`);
    requireString(entry.served, `${PROFILE_URL}: configFiles[].served`);
    if (!requireString(entry.fsPath, `${PROFILE_URL}: configFiles[].fsPath`).startsWith("/")) {
      throw new LoaderError(`${PROFILE_URL}: configFiles fsPath '${entry.fsPath}' is not absolute`);
    }
  }
  if (!Array.isArray(profile.engineArguments) || profile.engineArguments.length === 0) {
    throw new LoaderError(`${PROFILE_URL}: engineArguments must be a non-empty list`);
  }
  for (const argument of profile.engineArguments) {
    requireString(argument, `${PROFILE_URL}: engineArguments entry`);
  }
  const roles = new Map();
  for (const artifact of profile.artifacts) {
    requireObject(artifact, `${PROFILE_URL}: artifact`);
    const role = requireString(artifact.role, `${PROFILE_URL}: artifact role`);
    if (!ARTIFACT_ROLES.has(role)) {
      throw new LoaderError(`${PROFILE_URL}: unknown artifact role '${role}'`);
    }
    if (role !== "filesystem") {
      if (roles.has(role)) {
        throw new LoaderError(`${PROFILE_URL}: role '${role}' is declared twice`);
      }
      roles.set(role, artifact);
    } else {
      requireString(artifact.fsPath, `${PROFILE_URL}: artifact fsPath`);
      if (!artifact.fsPath.startsWith("/")) {
        throw new LoaderError(`${PROFILE_URL}: fsPath '${artifact.fsPath}' is not absolute`);
      }
    }
  }
  for (const role of ["module-script", "module-wasm"]) {
    if (!roles.has(role)) {
      throw new LoaderError(`${PROFILE_URL}: no artifact declares role '${role}'`);
    }
  }
  // Two entries sharing a served path would silently overwrite each other's
  // bytes in memory, so the verified digest would no longer describe what the
  // engine reads. The staging script refuses this too; refusing it here as well
  // keeps the loader safe against a served tree it did not assemble.
  const served = new Set();
  for (const entry of [...profile.artifacts, ...profile.configFiles]) {
    const path = requireString(entry.served, `${PROFILE_URL}: served`);
    if (served.has(path)) {
      throw new LoaderError(`${PROFILE_URL}: '${path}' is served twice`);
    }
    served.add(path);
  }
  return profile;
}

function exactKeys(value, expected, what) {
  const actual = Object.keys(requireObject(value, what)).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new LoaderError(`${what}: unexpected key set`);
  }
}

function parseRelayProfile(profile) {
  const keys = [
    "$comment",
    "connectFamily",
    "cvars",
    "formatVersion",
    "fragmentSize",
    "innerDatagramFloor",
    "keepAliveIntervalSource",
    "mode",
    "playerSettingNotes",
    "playerSettings",
    "receiveQueueDepth",
    "singleDatagramOverhead",
  ];
  exactKeys(profile, keys, RELAY_PROFILE_URL);
  if (
    profile.formatVersion !== 1 ||
    profile.mode !== "relay-client" ||
    profile.connectFamily !== "-6" ||
    profile.fragmentSize !== 704 ||
    profile.innerDatagramFloor !== INNER_DATAGRAM_FLOOR ||
    profile.receiveQueueDepth !== RECEIVE_QUEUE_DEPTH ||
    profile.singleDatagramOverhead !== 42 ||
    profile.keepAliveIntervalSource !== "runtime"
  ) {
    throw new LoaderError(`${RELAY_PROFILE_URL}: does not match the decided WP7 profile`);
  }
  const requiredCvars = {
    bot_enable: "0",
    cl_allowDownload: "0",
    cl_motd: "0",
    cl_voip: "0",
    com_basegame: "arena",
    com_legacyprotocol: "0",
    net_enabled: "2",
    r_allowResize: "1",
    r_fullscreen: "0",
    sv_pure: "0",
  };
  exactKeys(profile.cvars, Object.keys(requiredCvars), `${RELAY_PROFILE_URL}: cvars`);
  for (const [name, expected] of Object.entries(requiredCvars)) {
    if (profile.cvars[name] !== expected) {
      throw new LoaderError(`${RELAY_PROFILE_URL}: cvars.${name} is not '${expected}'`);
    }
  }
  // The player's own two choices are runtime inputs, so what the profile
  // carries is not a value but a bound. `scripts/arena_runtime.py` checks each
  // bound against the thing that derives it — the model list against the
  // packaged set, the length against the pinned `MAX_NETNAME` — and this side
  // reads them rather than restating either.
  exactKeys(profile.playerSettings, ["models", "name"], `${RELAY_PROFILE_URL}: playerSettings`);
  const models = profile.playerSettings.models;
  if (!Array.isArray(models) || models.length === 0) {
    throw new LoaderError(`${RELAY_PROFILE_URL}: playerSettings.models must be a non-empty list`);
  }
  for (const model of models) {
    requireString(model, `${RELAY_PROFILE_URL}: playerSettings.models entry`);
  }
  exactKeys(
    profile.playerSettings.name,
    ["maxLength", "minLength"],
    `${RELAY_PROFILE_URL}: playerSettings.name`,
  );
  for (const key of ["maxLength", "minLength"]) {
    const value = profile.playerSettings.name[key];
    if (!Number.isInteger(value) || value < 1) {
      throw new LoaderError(
        `${RELAY_PROFILE_URL}: playerSettings.name.${key} must be a positive integer`,
      );
    }
  }
  return profile;
}

function relayDestination(configuration) {
  const hex = configuration.destinationAddressHex;
  if (typeof hex !== "string" || !/^[0-9a-fA-F]{32}$/.test(hex)) {
    throw new LoaderError("relay destination is not a 16-byte hexadecimal address");
  }
  const port = configuration.destinationPort;
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new LoaderError("relay destination port is outside its accepted range");
  }
  const groups = [];
  for (let index = 0; index < hex.length; index += 4) {
    groups.push(hex.slice(index, index + 4));
  }
  return `[${groups.join(":")}]:${port}`;
}

// The player's own two inputs live in `arena/player-input.js`, which is a
// module of its own so that a test can run it under Node beside
// `scripts/arena_runtime.py`'s copy of the same rule. All this side does is
// give a refusal the loader's own error type.
function playerArguments(profile, configuration) {
  try {
    return playerLaunchArguments(profile, configuration);
  } catch (error) {
    throw new LoaderError(String(error?.message ?? error));
  }
}

function relayEngineArguments(profile, configuration) {
  const arguments_ = [];
  for (const name of Object.keys(profile.cvars).sort()) {
    arguments_.push("+set", name, profile.cvars[name]);
  }
  const player = playerArguments(profile, configuration);
  arguments_.push(...player);
  arguments_.push("+connect", profile.connectFamily, relayDestination(configuration));
  // Which positions carry a runtime value, so the evidence copy can redact
  // them by index. The redaction used to be positional — "everything but the
  // last argument" — which is exactly the kind of rule that quietly redacts
  // the wrong thing the moment an argument is added after it. The name is a
  // runtime input of the same class as the destination and must never be
  // reported; the model is not, because it is a choice from a committed set
  // and knowing which one a client registered is evidence rather than
  // disclosure.
  const redactions = new Map([
    [arguments_.length - 1, "[relay destination]"],
    [arguments_.length - 4, "[player name]"],
  ]);
  return { arguments: arguments_, redactions };
}

// Which archives this page is being opened for, and therefore which artifacts
// are fetched at all. Everything else about the release is unchanged: the
// committed profile still declares every published archive, every artifact
// fetched below is still verified against the committed manifest, and only the
// *selection* from that committed set is made here.
//
// The rotation is required rather than defaulted. There is no safe default:
// falling back to the whole published set makes every player wait for maps
// their server will never rotate to — the reason this selection exists — and
// falling back to the profile's own map produces the failure that is worse
// than slow, a client whose archive set is a strict subset of the server's
// rotation. That one is invisible until rotation reaches the missing map and
// then drops the client mid-match, because `sv_pure 0` and
// `cl_allowDownload 0` leave the engine no content-agreement check at all.
//
// `docs/integration-contract.md` states the rule the caller must satisfy: hold
// exactly one rotation list, and derive both the server's launch arguments and
// this parameter from it. arena-web cannot check that relation — it holds
// neither input, and comparing the caller's input with itself would always
// pass. It can check that a choice was made at all, and this is that check.
function selectArtifacts(profile, manifests) {
  const content = manifests.get("content");
  const byMap = new Map();
  let base = null;
  for (const artifact of profile.artifacts) {
    if (artifact.manifest !== "content") {
      continue;
    }
    const entry = content?.get(artifact.path);
    if (!entry) {
      throw new LoaderError(`content manifest: does not declare '${artifact.path}'`);
    }
    if (typeof entry.map === "string" && entry.map !== "") {
      if (byMap.has(entry.map)) {
        throw new LoaderError(`content manifest: declares map '${entry.map}' twice`);
      }
      byMap.set(entry.map, artifact);
    } else if (base === null) {
      base = artifact;
    } else {
      throw new LoaderError("content manifest: declares more than one base archive");
    }
  }
  if (base === null) {
    throw new LoaderError("content manifest: declares no base archive");
  }
  const published = [...byMap.keys()].sort();
  const parameter = new URL(window.location.href).searchParams.get(ROTATION_PARAMETER);
  if (parameter === null) {
    throw new LoaderError(
      `this page must be opened with ?${ROTATION_PARAMETER}=<map>[,<map>...], naming ` +
        `the maps the server will rotate through; it published ${published.join(", ")}`,
    );
  }
  const requested = parameter.split(",").map((name) => name.trim());
  if (requested.some((name) => name === "")) {
    throw new LoaderError(`?${ROTATION_PARAMETER}: is empty or has an empty entry`);
  }
  const unknown = requested.filter((name) => !byMap.has(name));
  if (unknown.length > 0) {
    // Never silently skipped: a dropped name looks exactly like a successful
    // selection and only shows up when rotation reaches that map.
    throw new LoaderError(
      `?${ROTATION_PARAMETER}: this release publishes no archive for ` +
        `${unknown.join(", ")}; it published ${published.join(", ")}`,
    );
  }
  // Canonical, because the caller passes its rotation *list* and a rotation may
  // legitimately play the same map more than once per cycle. Two spellings of
  // one set must fetch one set.
  const resolved = [...new Set(requested)].sort();
  // Where the rotation *starts*, as the caller wrote it. The fetch set is the
  // canonical one above, but a rotation is an ordered list and its first entry
  // is the map that comes up first — which is how the dedicated server reads
  // the same list (scripts/arena_runtime.py rotation_arguments), so the two
  // halves cannot disagree about where a session begins.
  const startMap = requested[0];
  const selected = [
    ...profile.artifacts.filter((artifact) => artifact.manifest !== "content"),
    // The base is implicit and can never be named: it carries no map, so its
    // archive name is not a valid selection and no rotation can leave it out.
    base,
    ...resolved.map((name) => byMap.get(name)),
  ];
  report.rotation = {
    parameter,
    requested,
    resolved,
    startMap,
    published,
    // What this selection *chose*. `fetched` below is what actually arrived,
    // which differs for a run that failed partway; the two are separate on
    // purpose, because a post-mortem needs to tell those apart.
    archives: selected
      .filter((artifact) => artifact.manifest === "content")
      .map((artifact) => artifact.path),
    fetched: [],
    missingOnServer: [],
  };
  return { selected, resolved, startMap };
}

// Load every selected artifact and prove it is the committed one. The digest is
// taken over the bytes this loader then uses: the engine module is imported
// from a blob of these bytes and the WebAssembly is instantiated from them, so
// the identity is the identity of what runs.
async function loadArtifacts(profile) {
  const manifests = new Map();
  for (const [name, path] of Object.entries(profile.manifests)) {
    const url = resolveServed(path, `${PROFILE_URL}: manifests.${name}`);
    manifests.set(name, manifestIndex(await fetchJson(url, path), path));
  }
  report.timings.manifestsLoadedMs = since();

  const { selected, resolved, startMap } = selectArtifacts(profile, manifests);
  const expectedTotal = selected.reduce((total, artifact) => {
    const index = manifests.get(artifact.manifest);
    if (!index) {
      throw new LoaderError(`${PROFILE_URL}: unknown manifest '${artifact.manifest}'`);
    }
    const entry = index.get(artifact.path);
    if (!entry) {
      throw new LoaderError(`${artifact.manifest} manifest: does not declare '${artifact.path}'`);
    }
    return total + entry.size;
  }, 0);

  const loaded = new Map();
  let completedBytes = 0;
  for (const artifact of selected) {
    const entry = manifests.get(artifact.manifest).get(artifact.path);
    const url = resolveServed(artifact.served, `${PROFILE_URL}: artifact served`);
    setMessage(`Loading ${artifact.path}`);
    const fetchStarted = since();
    const bytes = await fetchBytes(url, artifact.served, (received) => {
      setProgress(
        (completedBytes + received) / expectedTotal,
        completedBytes + received,
        expectedTotal,
      );
    });
    const fetchedAt = since();
    const digest = await sha256Hex(bytes);
    const identity = {
      manifest: artifact.manifest,
      path: artifact.path,
      served: artifact.served,
      expectedSha256: entry.sha256,
      actualSha256: digest,
      expectedSize: entry.size,
      actualSize: bytes.length,
      fetchMs: Math.round((fetchedAt - fetchStarted) * 1000) / 1000,
      matches: digest === entry.sha256 && bytes.length === entry.size,
    };
    report.identities.push(identity);
    if (!identity.matches) {
      throw new LoaderError(
        `${artifact.served}: identity does not match ${artifact.manifest} manifest ` +
          `(expected sha256:${entry.sha256} ${entry.size} bytes, got sha256:${digest} ${bytes.length} bytes)`,
      );
    }
    completedBytes += bytes.length;
    loaded.set(artifact.served, bytes);
    if (artifact.manifest === "content") {
      report.rotation.fetched.push(artifact.path);
    }
  }
  // The product's own engine configuration. It carries no committed digest of
  // its own — it is repository source, staged and byte-compared by
  // scripts/arena_runtime.py — so its identity is recorded, not asserted.
  for (const entry of profile.configFiles) {
    const url = resolveServed(entry.served, `${PROFILE_URL}: configFiles served`);
    const bytes = await fetchBytes(url, entry.served, () => {});
    report.configFiles.push({
      served: entry.served,
      fsPath: entry.fsPath,
      sha256: await sha256Hex(bytes),
      size: bytes.length,
    });
    loaded.set(entry.served, bytes);
  }

  report.totalArtifactBytes = completedBytes;
  // A final fetch callback may already have reported fraction 1 while its
  // digest is still being computed. Only this post-comparison publication is
  // allowed to claim the explicit verified phase.
  setProgress(1, completedBytes, expectedTotal, "verified");
  return { bytes: loaded, selected, resolved, startMap };
}

// Quake III colour codes are '^' followed by any character other than '^'
// (ioq3 code/qcommon/q_shared.h, Q_IsColorString). The engine prints
// "<netname>^7 entered the game", so a name comparison has to see through them.
function stripColorCodes(line) {
  return line.replace(/\^[^^]/g, "");
}

function recordEngineLine(line, stream, markers, botNames) {
  if (report.engineLog.length < ENGINE_LOG_LIMIT) {
    report.engineLog.push(stream === "err" ? `[stderr] ${line}` : line);
  } else {
    report.engineLogDropped += 1;
  }

  // The rotation was smaller than the server's, and the engine has just said
  // so. Only the marker line is recorded: the archive names follow it in the
  // engine's own output, which `engineLog()` already carries in full, and
  // scraping an unbounded number of following lines would capture whatever
  // else the console happened to print next.
  if (report.rotation !== null && line.includes(MISSING_PAK_MARKER)) {
    report.rotation.missingOnServer.push(line);
    note("server-referenced-file-missing", null);
  }

  // Which bot joined, and when. The generic "entered the game" marker below
  // cannot answer that: the engine prints it for every client and the local
  // player is always first (ioq3 code/game/g_client.c:1026).
  const plain = stripColorCodes(line).trim();
  for (const name of botNames) {
    if (plain === `${name} entered the game` && !report.botEntries.some((entry) => entry.name === name)) {
      report.botEntries.push({ name, at: since() });
      note("bot-entered-game", name);
    }
  }

  for (const [name, needle, how] of markers) {
    const hit = how === "endsWith" ? plain.endsWith(needle) : line.includes(needle);
    if (report.markers[name] === undefined && hit) {
      report.markers[name] = since();
      note("engine-marker", name);
      // The client game module printing its init time is the engine's own
      // statement that the map is entered and the frame loop is live
      // (ioq3 code/client/cl_cgame.c CL_InitCGame).
      if (name === "clientGameLoaded") {
        engineStarted = true;
        if (!["reconnect-ready", "reconnecting", "stopping", "failed", "exited"].includes(report.status)) {
          setStatus("running");
        } else {
          lifecycle.publish();
        }
        elements.hint.hidden = report.pointerLock.engaged;
      } else {
        lifecycle.publish();
      }
    }
  }
}

// The engine's internal resolution. It is derived from the live canvas box
// rather than committed, because a committed value would be an
// environment-specific one. r_mode -1 selects r_customwidth/r_customheight
// (ioq3 code/renderergl2/tr_init.c R_GetModeInfo); the floor matches the
// integer truncation SDL applies when it reports the canvas CSS size
// (SDL_emscriptenvideo.c Emscripten_CreateWindow).
function initialRenderSizeArguments() {
  const size = measureCanvas(elements.canvas);
  report.render = {
    ...size,
    startupCssWidth: size.cssWidth,
    startupCssHeight: size.cssHeight,
    resizeEvents: 0,
    observerSupported: false,
  };
  return canvasRenderSizeArguments(size);
}

function installCanvasResizeBridge() {
  canvasResizeBridge?.disconnect();
  canvasResizeBridge = observeCanvasResize(elements.canvas, {
    onResize: (size) => {
      report.render = {
        ...report.render,
        ...size,
        resizeEvents: (report.render?.resizeEvents ?? 0) + 1,
      };
      note("canvas-resize", {
        cssWidth: size.cssWidth,
        cssHeight: size.cssHeight,
        devicePixelRatio: size.devicePixelRatio,
      });
      lifecycle.publish();
    },
  });
  report.render.observerSupported = canvasResizeBridge.supported;
  note("canvas-resize-observer", canvasResizeBridge.supported);
}

function startFrameSampling() {
  let previous = performance.now();
  const tick = (now) => {
    const delta = now - previous;
    previous = now;
    if (frameDeltas.length < FRAME_SAMPLE_LIMIT) {
      frameDeltas.push(delta);
    } else {
      report.frames.dropped += 1;
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function frameStatistics() {
  const samples = frameDeltas.filter((value) => Number.isFinite(value) && value >= 0);
  if (samples.length === 0) {
    return { samples: 0, dropped: report.frames.dropped };
  }
  const sorted = [...samples].sort((left, right) => left - right);
  const at = (fraction) => sorted[Math.min(sorted.length - 1, Math.floor(fraction * sorted.length))];
  const total = sorted.reduce((sum, value) => sum + value, 0);
  const round = (value) => Math.round(value * 100) / 100;
  return {
    samples: sorted.length,
    dropped: report.frames.dropped,
    meanMs: round(total / sorted.length),
    medianMs: round(at(0.5)),
    p95Ms: round(at(0.95)),
    maxMs: round(sorted[sorted.length - 1]),
    longFrames: sorted.filter((value) => value > LONG_FRAME_MS).length,
    meanFps: round(1000 / (total / sorted.length)),
  };
}

function focusSurface() {
  if (typeof elements.canvas.focus !== "function") {
    return { ok: false, focused: false, reason: "not_supported" };
  }
  try {
    elements.canvas.focus({ preventScroll: true });
    const focused = document.activeElement === elements.canvas;
    note("surface-focus", focused);
    lifecycle.publish();
    return { ok: focused, focused, reason: focused ? null : "not_focused" };
  } catch (error) {
    note("surface-focus-rejected", String(error?.name ?? "Error"));
    return { ok: false, focused: false, reason: String(error?.name ?? "Error").slice(0, 80) };
  }
}

async function setFullscreen(engaged) {
  if (typeof engaged !== "boolean") {
    throw new LoaderError("fullscreen state must be a boolean");
  }
  const current = document.fullscreenElement === elements.stage;
  if (current === engaged) {
    return { ok: true, engaged: current, reason: null };
  }
  if (engaged && typeof elements.stage.requestFullscreen !== "function") {
    return { ok: false, engaged: current, reason: "not_supported" };
  }
  if (!engaged && typeof document.exitFullscreen !== "function") {
    return { ok: false, engaged: current, reason: "not_supported" };
  }
  try {
    // The stage carries the canvas at 100% of its box, so SDL's resize
    // callback reads the new CSS size and the engine follows.
    if (engaged) {
      await elements.stage.requestFullscreen();
    } else {
      await document.exitFullscreen();
    }
    const actual = document.fullscreenElement === elements.stage;
    return {
      ok: actual === engaged,
      engaged: actual,
      reason: actual === engaged ? null : "state_mismatch",
    };
  } catch (error) {
    const reason = String(error?.name ?? "Error").slice(0, 80);
    note("fullscreen-rejected", reason);
    return {
      ok: false,
      engaged: document.fullscreenElement === elements.stage,
      reason,
    };
  }
}

function installPageBehaviour() {
  // Pointer lock belongs to SDL: the engine asks for relative mouse mode and
  // SDL's Emscripten backend defers the request to the next user gesture
  // (SDL_emscriptenmouse.c Emscripten_SetRelativeMouseMode). The loader owns
  // only the user-visible half — telling the player that a click captures the
  // mouse, and noticing when the capture is lost.
  document.addEventListener("pointerlockchange", () => {
    report.pointerLock.engaged = document.pointerLockElement === elements.canvas;
    elements.hint.hidden = report.pointerLock.engaged || report.status !== "running";
    note("pointerlockchange", report.pointerLock.engaged);
    lifecycle.publish();
  });
  document.addEventListener("pointerlockerror", () => {
    report.pointerLock.errors += 1;
    note("pointerlockerror", null);
    lifecycle.publish();
  });

  // SDL asks for the lock with a bare `requestPointerLock()` — its Emscripten
  // backend calls Emscripten's helper, which calls the method with no options —
  // and that leaves the engine reading the pointer deltas the operating system
  // has already accelerated. An arena shooter wants the unaccelerated ones: with
  // acceleration the same physical movement turns a different amount depending
  // on how fast it was made, which is the one thing aim has to be able to rely
  // on. `unadjustedMovement` is how that is asked for, and neither SDL nor
  // Emscripten has a way to pass it, so the canvas's own method carries it.
  //
  // The instance's method rather than the prototype's, so this document's other
  // elements keep the platform's behaviour, and it is installed here rather than
  // at the boot site because SDL defers its request to a later user gesture and
  // this has to already be in place by then.
  //
  // It is a request and not a requirement. A platform without raw deltas rejects
  // it, and the answer to that is the ordinary lock rather than no lock at all —
  // an unaccelerated pointer is better aim, a missing pointer lock is not a
  // playable game.
  const requestLock = elements.canvas.requestPointerLock.bind(elements.canvas);
  elements.canvas.requestPointerLock = function requestUnadjustedPointerLock() {
    const settle = (unadjusted) => {
      report.pointerLock.unadjusted = unadjusted;
      note("pointerlock-unadjusted", unadjusted);
      lifecycle.publish();
    };
    let attempt;
    try {
      attempt = requestLock({ unadjustedMovement: true });
    } catch (_) {
      // A browser that rejects the argument shape outright, rather than by
      // returning a promise that rejects.
      settle(false);
      return requestLock();
    }
    if (attempt === undefined || typeof attempt.then !== "function") {
      // The pre-promise form: the option was ignored and there is no way to
      // learn whether it took effect, so nothing is claimed about it.
      return attempt;
    }
    return attempt.then(
      () => settle(true),
      () => {
        settle(false);
        return requestLock();
      },
    );
  };

  // A lost drawing context stops the engine dead and is otherwise invisible in
  // the engine's own console, so the loader watches for it.
  elements.canvas.addEventListener("webglcontextlost", (event) => {
    report.webglContextLost += 1;
    note("webglcontextlost", String(event.statusMessage ?? ""));
    console.error("arena-web: the WebGL context was lost");
  });
  elements.canvas.addEventListener("webglcontextrestored", () => note("webglcontextrestored", null));

  // The canvas is the game surface: the browser's own context menu and text
  // selection would otherwise interrupt aiming and firing.
  elements.canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  elements.canvas.addEventListener("mousedown", () => focusSurface());

  elements.fullscreen.addEventListener("click", () => {
    void setFullscreen(document.fullscreenElement !== elements.stage);
  });
  document.addEventListener("fullscreenchange", () => {
    report.fullscreen.engaged = document.fullscreenElement === elements.stage;
    note("fullscreenchange", report.fullscreen.engaged);
    lifecycle.publish();
  });

  window.addEventListener("blur", () => note("window-blur", null));
  window.addEventListener("focus", () => note("window-focus", null));
  document.addEventListener("visibilitychange", () => note("visibilitychange", document.visibilityState));
  window.addEventListener("error", (event) => {
    report.browserErrors.push({ at: since(), message: String(event.message) });
  });
  window.addEventListener("unhandledrejection", (event) => {
    report.browserErrors.push({ at: since(), message: `unhandled rejection: ${String(event.reason)}` });
  });
}

// Proof that the page has audio activation at the moment the engine boots. The
// engine's own contexts are created a moment later, inside the same sticky
// activation, and the Emscripten runtime resumes them itself
// (autoResumeAudioContext in the emitted ioquake3.js).
async function recordAudioActivation() {
  const AudioContextClass = globalThis.AudioContext ?? globalThis.webkitAudioContext;
  if (!AudioContextClass) {
    report.audioActivation = { available: false };
    return;
  }
  const context = new AudioContextClass();
  report.audioActivation = {
    available: true,
    state: context.state,
    userActivation: Boolean(navigator.userActivation?.hasBeenActive),
    sampleRate: context.sampleRate,
  };
  await context.close();
}

function requestEngineQuit() {
  if (engineQuitInvoked || engineQuit === null) {
    return false;
  }
  engineQuitInvoked = true;
  note("engine-quit-requested", null);
  engineQuit();
  return true;
}

function throwIfStopped() {
  if (stopRequested) {
    throw new DOMException("host stop requested", "AbortError");
  }
}

// ioq3 code/sys/sys_main.c main(): argv is concatenated into one fixed buffer,
// an argument containing a space is quoted, each is followed by a space. Both
// ceilings below are silent — Q_strcat truncates through Q_strncpyz, and
// Com_ParseCommandLine returns once it holds MAX_CONSOLE_LINES — so an
// argument list that does not fit boots an engine that is subtly not the one
// this page asked for. Refused here instead, before the module is imported.
function engineCommandLineOf(argv) {
  return argv.map((argument) => (argument.includes(" ") ? `"${argument}" ` : `${argument} `)).join("");
}

function engineConsoleLinesOf(argv) {
  let lines = 1;
  let quoted = false;
  for (const character of engineCommandLineOf(argv)) {
    if (character === '"') {
      quoted = !quoted;
    } else if (character === "+" && !quoted) {
      lines += 1;
    }
  }
  return lines;
}

function checkCommandLineBudget(argv, limits) {
  const size = engineCommandLineOf(argv).length;
  if (size >= limits.maxBytes) {
    throw new LoaderError(
      `the engine command line is ${size} bytes against the pinned engine's ` +
        `${limits.maxBytes}-byte buffer, which truncates in silence`,
    );
  }
  const lines = engineConsoleLinesOf(argv);
  if (lines > limits.maxLines) {
    throw new LoaderError(
      `the engine command line is ${lines} console lines against the pinned ` +
        `engine's ${limits.maxLines}, past which nothing is parsed or reported`,
    );
  }
}

async function boot(profile, artifacts, networkBackend = null) {
  throwIfStopped();
  // The map is a launch argument, so the marker that names it is a template.
  // A relay client starts no map of its own and never prints the line at all,
  // so a marker that still carried the placeholder there would be a needle
  // that cannot match; it is dropped rather than left in as one.
  const markers = Object.entries(profile.readyMarkers).flatMap(([name, needle]) => {
    if (!needle.includes(MARKER_MAP_PLACEHOLDER)) {
      return [[name, needle, "includes"]];
    }
    if (loadedStartMap === null || networkBackend) {
      return [];
    }
    // Anchored at the end of the line, not matched as a substring. The engine
    // prints `Server: %s` with the map last (ioq3 code/server/sv_init.c), and
    // one published map name can be a prefix of another — `am_galmevish` and
    // `am_galmevish2` are both in this release — so a substring match would let
    // the wrong spawn satisfy this marker. The marker's whole claim is that the
    // map that came up is the one that was asked for, so it has to be exact.
    return [[name, needle.split(MARKER_MAP_PLACEHOLDER).join(loadedStartMap), "endsWith"]];
  });
  const botNames = networkBackend ? [] : profile.bots.map((bot) => bot.name);
  const byRole = new Map();
  for (const artifact of profile.artifacts) {
    if (artifact.role !== "filesystem") {
      byRole.set(artifact.role, artifact);
    }
  }
  const scriptBytes = artifacts.get(byRole.get("module-script").served);
  const wasmBytes = artifacts.get(byRole.get("module-wasm").served);
  // The selected set, not the declared one: an archive that was not fetched
  // has no bytes to write, and writing a hole would be worse than not having
  // it — the engine would mount a truncated PK3 rather than none.
  const filesystemArtifacts = loadedSelection.filter(
    (artifact) => artifact.role === "filesystem",
  );

  // The offline slice's map, prepended rather than spliced: every `+set` line
  // is applied by Com_StartupVariable before the command buffer runs at all
  // (ioq3 code/qcommon/common.c Com_Init), so `+map` still precedes every
  // `+addbot` in buffer order, which is what Svcmd_AddBot_f needs. A relay
  // client starts no map — its rotation is the server's.
  const relayLaunch = networkBackend
    ? relayEngineArguments(relayProfile, relayRuntimeConfiguration)
    : null;
  const profileArguments =
    relayLaunch?.arguments ?? ["+map", loadedStartMap, ...profile.engineArguments];
  const engineArguments = [...profileArguments, ...initialRenderSizeArguments()];
  checkCommandLineBudget(engineArguments, profile.engineCommandLine);
  // The record is a copy: Emscripten's callMain unshifts the program name onto
  // the array it is given, so handing the engine this exact array would edit
  // the evidence. The runtime values are replaced by index rather than by
  // position or by value — by position it would redact whatever happened to be
  // last, and by value a player whose name is "0" would blank every "0" in the
  // record.
  report.engineArguments = engineArguments.map((argument, index) =>
    relayLaunch?.redactions.get(index) ?? argument,
  );

  const moduleUrl = URL.createObjectURL(new Blob([scriptBytes], { type: "text/javascript" }));
  let factory;
  try {
    factory = (await import(moduleUrl)).default;
  } finally {
    URL.revokeObjectURL(moduleUrl);
  }
  throwIfStopped();
  report.timings.moduleImportedMs = since();

  const configuration = {
    canvas: elements.canvas,
    arguments: [...engineArguments],
    arenaNetwork: networkBackend ?? undefined,
    // Every artifact is already in memory and verified, so nothing should be
    // located and fetched a second time. A request is recorded rather than
    // silently answered, because an extra fetch is exactly what the
    // declared-artifacts evidence has to be able to see.
    locateFile: (path) => {
      report.unexpectedFileRequests.push({ at: since(), path });
      note("unexpected-locate-file", path);
      return path;
    },
    instantiateWasm: (imports, onInstantiated) => {
      WebAssembly.instantiate(wasmBytes, imports).then(
        (result) => onInstantiated(result.instance, result.module),
        (error) => fail(error),
      );
      return {};
    },
    preRun: [
      (module) => {
        const write = (fsPath, bytes) => {
          const directory = fsPath.slice(0, fsPath.lastIndexOf("/")) || "/";
          module.FS.mkdirTree(directory);
          module.FS.writeFile(fsPath, bytes);
        };
        for (const artifact of filesystemArtifacts) {
          write(artifact.fsPath, artifacts.get(artifact.served));
        }
        for (const entry of profile.configFiles) {
          write(entry.fsPath, artifacts.get(entry.served));
        }
        report.timings.filesystemPopulatedMs = since();
      },
    ],
    print: (line) => recordEngineLine(line, "out", markers, botNames),
    printErr: (line) => recordEngineLine(line, "err", markers, botNames),
    onRuntimeInitialized: () => {
      report.timings.runtimeInitializedMs = since();
      note("runtime-initialized", null);
      if (typeof configuration._Web_RequestQuit !== "function") {
        fail(new LoaderError("engine exposes no host stop function"), "engine_contract_error");
        return;
      }
      engineQuit = () => configuration._Web_RequestQuit();
      installCanvasResizeBridge();
      lifecycle.publish();
      if (stopRequested) {
        requestEngineQuit();
      }
    },
    onAbort: (what) => fail(new LoaderError(`engine aborted: ${what}`), "engine_abort"),
    onExit: (code) => {
      canvasResizeBridge?.disconnect();
      canvasResizeBridge = null;
      note("engine-exit", code);
      if (lifecycle.terminal() === null) {
        settle("exited", Number.isInteger(code) ? code : null, stopRequested ? "host_stop" : "engine_exit");
      }
    },
  };

  report.timings.engineStartedMs = since();
  engineBootStarted = true;
  factory(configuration).then(
    () => note("engine-main-returned", null),
    (error) => fail(error, "engine_error"),
  );
}

function snapshot() {
  const value = {
    status: report.status,
    error: report.error,
    exit: report.exit,
    progress: report.progress,
    profile: report.profile,
    identities: report.identities,
    configFiles: report.configFiles,
    botEntries: report.botEntries,
    totalArtifactBytes: report.totalArtifactBytes,
    timings: report.timings,
    markers: report.markers,
    engineArguments: report.engineArguments,
    engineLogLines: report.engineLog.length,
    engineLogDropped: report.engineLogDropped,
    browserErrors: report.browserErrors,
    rotation: report.rotation,
    frames: frameStatistics(),
    events: report.events,
    eventsDropped: report.eventsDropped,
    unexpectedFileRequests: report.unexpectedFileRequests,
    audioActivation: report.audioActivation,
    pointerLock: report.pointerLock,
    fullscreen: report.fullscreen,
    surface: {
      selector: '[data-runtime-surface="arena-web"]',
      focused: document.activeElement === elements.canvas,
    },
    webglContextLost: report.webglContextLost,
    render: report.render,
    mode: report.mode,
    relay: relayBackend?.snapshot() ?? null,
  };
  // A consumer may retain or mutate its snapshot; neither operation can edit
  // the loader's live evidence or a later subscriber notification.
  return structuredClone(value);
}

function configureRelay(configuration) {
  if (!["starting", "ready"].includes(report.status) || startAccepted) {
    throw new LoaderError("relay configuration must be supplied before Start");
  }
  requireObject(configuration, "relay runtime configuration");
  // Validate the player's two inputs here, for the refusal rather than the
  // result: a consumer that gets a bad name back at `start()` has to work out
  // which of the two calls was wrong, and the one that was wrong is this one.
  // It is conditional only because `configureRelay` is accepted from `starting`
  // as well, where the profile may not be parsed yet; `boot` validates again in
  // every case, so nothing depends on this having run.
  if (relayProfile !== null) {
    playerArguments(relayProfile, configuration);
  }
  relayRuntimeConfiguration = {
    ...configuration,
    certificateHashes: Array.isArray(configuration.certificateHashes)
      ? [...configuration.certificateHashes]
      : configuration.certificateHashes,
  };
  report.mode = "relay";
  note("relay-configured", null);
  lifecycle.publish();
}

async function runStart() {
  setStatus("booting");
  report.timings.startClickedMs = since();
  elements.start.disabled = true;
  elements.overlay.hidden = true;
  focusSurface();
  try {
    await recordAudioActivation();
    throwIfStopped();
    if (relayRuntimeConfiguration !== null) {
      relayBackend = new ArenaNetworkSession(relayRuntimeConfiguration, {
        onEvent: (kind, detail) => {
          note(kind, detail);
          lifecycle.publish();
          if (
            kind === "relay-terminal" &&
            !["client_close", "engine_shutdown"].includes(detail) &&
            !stopRequested &&
            !["failed", "exited"].includes(report.status)
          ) {
            offerRelayReconnect(detail);
          }
        },
      });
      await relayBackend.open();
      throwIfStopped();
      report.relay = relayBackend.snapshot();
      lifecycle.publish();
    }
    await boot(loadedProfile, loadedArtifacts, relayBackend);
    engineStarted = true;
    if (relayBackend?.snapshot().state === "closed") {
      offerRelayReconnect(relayBackend.snapshot().terminalReason);
    }
    return snapshot();
  } catch (error) {
    if (stopRequested && error?.name === "AbortError") {
      if (!engineBootStarted && lifecycle.terminal() === null) {
        settle("exited", null, "host_stop");
      }
    } else {
      fail(error);
    }
    throw error;
  }
}

function start() {
  if (startAccepted) {
    return Promise.reject(new LoaderError("Start has already been accepted"));
  }
  if (report.status !== "ready" || loadedProfile === null || loadedArtifacts === null) {
    return Promise.reject(new LoaderError("Start is only available in the ready state"));
  }
  // There was a refusal here, for an offline profile whose committed `+map`
  // named a map the rotation had not fetched. It is gone rather than kept,
  // because the map is no longer committed: the offline slice starts the
  // rotation's own first entry, so the archive it needs is in the fetch set by
  // construction and the refusal could not fire. A check that cannot fail is
  // not a cheap guard, it is a claim that reads as one.
  if (navigator.userActivation?.isActive !== true) {
    return Promise.reject(new LoaderError("Start requires transient user activation"));
  }
  startAccepted = true;
  startOperation = runStart();
  return startOperation;
}

async function runStop() {
  stopRequested = true;
  startupAbort.abort();
  if (lifecycle.terminal() !== null) {
    return lifecycle.whenSettled();
  }
  setStatus("stopping");
  elements.start.disabled = true;
  elements.overlay.hidden = false;
  elements.hint.hidden = true;
  setMessage("Stopping the arena.");
  if (engineBootStarted) {
    // The engine is what disconnects, and it does it on the way out: Com_Quit_f
    // runs CL_Disconnect, which sends ioquake3's `disconnect` three times, and
    // only then shuts the relay down from inside (NET_Shutdown). Closing the
    // relay first refused exactly those datagrams, so every clean exit left the
    // server holding the client until its own sv_timeout — 200 seconds of a
    // ghost in the scoreboard and of return traffic aimed at nobody. The close
    // below is now a backstop for an engine that did not get there, which is
    // why it is still unconditional.
    requestEngineQuit();
    await settledWithin(ENGINE_QUIT_GRACE_MILLISECONDS);
  } else if (lifecycle.terminal() === null) {
    settle("exited", null, "host_stop");
  }
  try {
    await relayBackend?.close();
  } catch (error) {
    note("relay-close-failed", String(error?.name ?? "Error"));
  }
  return lifecycle.whenSettled();
}

// Resolves when the lifecycle settles or the grace expires, whichever comes
// first, and never rejects: the caller is a shutdown and has nothing to do
// with a refusal here except carry on stopping.
function settledWithin(milliseconds) {
  if (lifecycle.terminal() !== null) {
    return Promise.resolve();
  }
  let timer = null;
  const deadline = new Promise((resolve) => {
    timer = setTimeout(resolve, milliseconds);
  });
  return Promise.race([lifecycle.whenSettled(), deadline]).then(
    () => clearTimeout(timer),
    () => clearTimeout(timer),
  );
}

function stop() {
  if (stopOperation === null) {
    stopOperation = runStop();
  }
  return stopOperation;
}

globalThis.arenaWeb = Object.freeze({
  // `report` and `engineLog` remain acceptance diagnostics. Integrations use
  // the defensive snapshot/subscription boundary below.
  report,
  snapshot,
  subscribe: (listener) => lifecycle.subscribe(listener),
  whenSettled: () => lifecycle.whenSettled(),
  engineLog: () => report.engineLog.slice(),
  configureRelay,
  start,
  stop,
  focusSurface,
  setFullscreen,
  reconnectRelay,
});

async function main() {
  installPageBehaviour();
  startFrameSampling();
  setMessage("Reading the content configuration.");
  const profile = parseProfile(await fetchJson(resolveServed(PROFILE_URL, "profile"), PROFILE_URL));
  relayProfile = parseRelayProfile(
    await fetchJson(resolveServed(RELAY_PROFILE_URL, "relay profile"), RELAY_PROFILE_URL),
  );
  // The map is a launch argument, so what the profile identifies is the
  // release; `snapshot().rotation` is where the session's own maps are.
  report.profile = { package: profile.package, formatVersion: profile.formatVersion };
  report.timings.profileLoadedMs = since();

  const { bytes, selected, resolved, startMap } = await loadArtifacts(profile);
  report.timings.artifactsVerifiedMs = since();
  throwIfStopped();
  loadedProfile = profile;
  loadedArtifacts = bytes;
  loadedSelection = selected;
  loadedRotation = resolved;
  loadedStartMap = startMap;
  setStatus("ready");

  const megabytes = (report.totalArtifactBytes / (1024 * 1024)).toFixed(1);
  setMessage(
    `${selected.length} artifacts verified against the committed manifests (${megabytes} MiB).\n` +
      `Rotation: ${resolved.join(", ")}.\n` +
      `Press Start to enter ${startMap} with ${profile.bots?.length ?? 0} bots.`,
  );
  elements.start.disabled = false;
  elements.fullscreen.disabled = false;

  elements.start.addEventListener("click", () => {
    if (relayReconnectReady) {
      void reconnectRelay();
    } else {
      void start().catch((error) => {
        // start() can refuse before it has consumed anything, and the loader
        // is then still in the ready state, so the reason has to reach the
        // overlay or the click looks like it did nothing at all.
        //
        // Exactly one rejection still satisfies both halves of that condition:
        // a click carrying no transient user activation. A real click always
        // carries one, so the way in is a synthetic `element.click()` from a
        // host page or an embedder. The other two rejections cannot reach this
        // branch — `startAccepted` fails its own guard and a non-`ready` status
        // fails the other — and WP-E removed the rotation refusal that used to
        // be the interesting case. Kept because a Promise rejection nobody
        // renders is a button that silently does nothing, and exercised rather
        // than asserted: `start-refusal-is-visible` in scripts/arena_acceptance.py
        // dispatches that synthetic click in the pinned browser.
        if (report.status === "ready" && !startAccepted) {
          setMessage(`Cannot start: ${safeError(error).message}`);
        }
      });
    }
  });
}

main().catch((error) => {
  if (stopRequested && error?.name === "AbortError") {
    if (lifecycle.terminal() === null) {
      settle("exited", null, "host_stop");
    }
    return;
  }
  fail(error);
});
