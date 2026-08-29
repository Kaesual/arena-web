// SPDX-License-Identifier: GPL-2.0-or-later
//
// The product-owned browser loader of the offline one-map FFA slice.
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
//   4. boots the engine straight into the profile's FFA map with its bots and
//      records load timing, frame timing, the engine's own console output and
//      the runtime identities.
//
// It implements only the loader behaviour the work package names: canvas
// sizing, pointer lock, keyboard/mouse input, fullscreen and user-activated
// audio. There is no settings persistence, no OPFS, no account and no network
// backend.

const PROFILE_URL = "game-profile.json";
const ENGINE_LOG_LIMIT = 40000;
const FRAME_SAMPLE_LIMIT = 30000;
const LONG_FRAME_MS = 50;
const MINIMUM_RENDER_WIDTH = 320;
const MINIMUM_RENDER_HEIGHT = 240;

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
  totalArtifactBytes: 0,
  timings: { pageStartedAtEpochMs: Date.now() },
  markers: {},
  engineArguments: [],
  engineLog: [],
  engineLogDropped: 0,
  browserErrors: [],
  frames: { samples: 0, dropped: 0 },
  events: [],
  unexpectedFileRequests: [],
  audioActivation: null,
  pointerLock: { supported: "pointerLockElement" in document, engaged: false, errors: 0 },
  fullscreen: { supported: Boolean(elements.stage.requestFullscreen), engaged: false },
  webglContextLost: 0,
  render: null,
};

const frameDeltas = [];
const startedAt = performance.now();

function since() {
  return Math.round((performance.now() - startedAt) * 1000) / 1000;
}

function note(kind, detail) {
  report.events.push({ at: since(), kind, detail: detail ?? null });
}

function setMessage(text) {
  elements.message.textContent = text;
}

function setProgress(fraction) {
  const clamped = Math.max(0, Math.min(1, fraction));
  elements.progressBar.style.width = `${(clamped * 100).toFixed(1)}%`;
}

function fail(error) {
  report.status = "failed";
  report.error = { name: error.name ?? "Error", message: String(error.message ?? error) };
  elements.overlay.hidden = false;
  elements.start.disabled = true;
  setMessage(`Failed: ${report.error.message}`);
  // The browser console is part of the acceptance evidence, so a loader
  // failure has to be visible there and not only inside this page.
  console.error("arena-web loader failed", error);
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
  const response = await fetch(url);
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
  const response = await fetch(url);
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
  requireString(profile.map, `${PROFILE_URL}: map`);
  requireObject(profile.manifests, `${PROFILE_URL}: manifests`);
  requireObject(profile.readyMarkers, `${PROFILE_URL}: readyMarkers`);
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

// Load every declared artifact and prove it is the committed one. The digest is
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

  const expectedTotal = profile.artifacts.reduce((total, artifact) => {
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
  for (const artifact of profile.artifacts) {
    const entry = manifests.get(artifact.manifest).get(artifact.path);
    const url = resolveServed(artifact.served, `${PROFILE_URL}: artifact served`);
    setMessage(`Loading ${artifact.path}`);
    const fetchStarted = since();
    const bytes = await fetchBytes(url, artifact.served, (received) => {
      setProgress((completedBytes + received) / expectedTotal);
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
  setProgress(1);
  return loaded;
}

function recordEngineLine(line, stream, markers) {
  if (report.engineLog.length < ENGINE_LOG_LIMIT) {
    report.engineLog.push(stream === "err" ? `[stderr] ${line}` : line);
  } else {
    report.engineLogDropped += 1;
  }
  for (const [name, needle] of markers) {
    if (report.markers[name] === undefined && line.includes(needle)) {
      report.markers[name] = since();
      note("engine-marker", name);
      // The client game module printing its init time is the engine's own
      // statement that the map is entered and the frame loop is live
      // (ioq3 code/client/cl_cgame.c CL_InitCGame).
      if (name === "clientGameLoaded") {
        report.status = "running";
        elements.hint.hidden = report.pointerLock.engaged;
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
function renderSizeArguments() {
  const rect = elements.canvas.getBoundingClientRect();
  const width = Math.max(MINIMUM_RENDER_WIDTH, Math.floor(rect.width));
  const height = Math.max(MINIMUM_RENDER_HEIGHT, Math.floor(rect.height));
  report.render = {
    cssWidth: width,
    cssHeight: height,
    devicePixelRatio: globalThis.devicePixelRatio ?? 1,
  };
  return ["+set", "r_mode", "-1", "+set", "r_customwidth", String(width), "+set", "r_customheight", String(height)];
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
  });
  document.addEventListener("pointerlockerror", () => {
    report.pointerLock.errors += 1;
    note("pointerlockerror", null);
  });

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
  elements.canvas.addEventListener("mousedown", () => elements.canvas.focus());

  elements.fullscreen.addEventListener("click", () => {
    if (document.fullscreenElement) {
      document.exitFullscreen();
      return;
    }
    // The stage carries the canvas at 100% of its box, so SDL's resize
    // callback reads the new CSS size and the engine follows.
    elements.stage.requestFullscreen().catch((error) => {
      note("fullscreen-rejected", String(error.name ?? error));
    });
  });
  document.addEventListener("fullscreenchange", () => {
    report.fullscreen.engaged = document.fullscreenElement === elements.stage;
    note("fullscreenchange", report.fullscreen.engaged);
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

async function boot(profile, artifacts) {
  const markers = Object.entries(profile.readyMarkers);
  const byRole = new Map();
  for (const artifact of profile.artifacts) {
    if (artifact.role !== "filesystem") {
      byRole.set(artifact.role, artifact);
    }
  }
  const scriptBytes = artifacts.get(byRole.get("module-script").served);
  const wasmBytes = artifacts.get(byRole.get("module-wasm").served);
  const filesystemArtifacts = profile.artifacts.filter((artifact) => artifact.role === "filesystem");

  const engineArguments = [...profile.engineArguments, ...renderSizeArguments()];
  // The record is a copy: Emscripten's callMain unshifts the program name onto
  // the array it is given, so handing the engine this exact array would edit
  // the evidence.
  report.engineArguments = [...engineArguments];

  const moduleUrl = URL.createObjectURL(new Blob([scriptBytes], { type: "text/javascript" }));
  let factory;
  try {
    factory = (await import(moduleUrl)).default;
  } finally {
    URL.revokeObjectURL(moduleUrl);
  }
  report.timings.moduleImportedMs = since();

  const configuration = {
    canvas: elements.canvas,
    arguments: [...engineArguments],
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
    print: (line) => recordEngineLine(line, "out", markers),
    printErr: (line) => recordEngineLine(line, "err", markers),
    onRuntimeInitialized: () => {
      report.timings.runtimeInitializedMs = since();
      note("runtime-initialized", null);
    },
    onAbort: (what) => fail(new LoaderError(`engine aborted: ${what}`)),
    onExit: (code) => {
      note("engine-exit", code);
      report.status = "exited";
    },
  };

  report.timings.engineStartedMs = since();
  factory(configuration).then(
    () => note("engine-main-returned", null),
    (error) => fail(error),
  );
}

function snapshot() {
  return {
    status: report.status,
    error: report.error,
    profile: report.profile,
    identities: report.identities,
    configFiles: report.configFiles,
    totalArtifactBytes: report.totalArtifactBytes,
    timings: report.timings,
    markers: report.markers,
    engineArguments: report.engineArguments,
    engineLogLines: report.engineLog.length,
    engineLogDropped: report.engineLogDropped,
    browserErrors: report.browserErrors,
    frames: frameStatistics(),
    events: report.events,
    unexpectedFileRequests: report.unexpectedFileRequests,
    audioActivation: report.audioActivation,
    pointerLock: report.pointerLock,
    fullscreen: report.fullscreen,
    webglContextLost: report.webglContextLost,
    render: report.render,
  };
}

globalThis.arenaWeb = {
  report,
  snapshot,
  engineLog: () => report.engineLog.slice(),
};

async function main() {
  installPageBehaviour();
  startFrameSampling();
  setMessage("Reading the content configuration.");
  const profile = parseProfile(await fetchJson(resolveServed(PROFILE_URL, "profile"), PROFILE_URL));
  report.profile = { package: profile.package, map: profile.map, formatVersion: profile.formatVersion };
  report.timings.profileLoadedMs = since();

  const artifacts = await loadArtifacts(profile);
  report.timings.artifactsVerifiedMs = since();
  report.status = "ready";

  const megabytes = (report.totalArtifactBytes / (1024 * 1024)).toFixed(1);
  setMessage(
    `${profile.artifacts.length} artifacts verified against the committed manifests (${megabytes} MiB).\n` +
      `Press Start to enter ${profile.map} with ${profile.bots?.length ?? 0} bots.`,
  );
  elements.start.disabled = false;
  elements.fullscreen.disabled = false;

  elements.start.addEventListener(
    "click",
    () => {
      elements.start.disabled = true;
      elements.overlay.hidden = true;
      report.status = "booting";
      report.timings.startClickedMs = since();
      elements.canvas.focus();
      recordAudioActivation()
        .then(() => boot(profile, artifacts))
        .catch(fail);
    },
    { once: true },
  );
}

main().catch(fail);
