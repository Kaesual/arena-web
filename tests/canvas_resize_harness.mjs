// SPDX-License-Identifier: GPL-2.0-or-later
// Deterministic, browser-free checks for the canvas-to-SDL resize bridge.

import assert from "node:assert/strict";
import {
  MINIMUM_RENDER_HEIGHT,
  MINIMUM_RENDER_WIDTH,
  measureCanvas,
  observeCanvasResize,
  renderSizeArguments,
} from "../arena/canvas-resize.js";

let checks = 0;
let rectangle = { width: 1280.9, height: 577.8 };
const canvas = { getBoundingClientRect: () => ({ ...rectangle }) };

const initial = measureCanvas(canvas, 1.25);
assert.deepEqual(initial, {
  cssWidth: 1280,
  cssHeight: 577,
  devicePixelRatio: 1.25,
});
checks += 1;
assert.deepEqual(renderSizeArguments(initial), [
  "+set",
  "r_mode",
  "-1",
  "+set",
  "r_customwidth",
  "1280",
  "+set",
  "r_customheight",
  "577",
]);
checks += 1;

rectangle = { width: 100, height: 120 };
assert.deepEqual(measureCanvas(canvas, Number.NaN), {
  cssWidth: MINIMUM_RENDER_WIDTH,
  cssHeight: MINIMUM_RENDER_HEIGHT,
  devicePixelRatio: 1,
});
checks += 1;

let observer;
class FakeResizeObserver {
  constructor(callback) {
    this.callback = callback;
    this.observed = [];
    this.disconnected = false;
    observer = this;
  }

  observe(target) {
    this.observed.push(target);
  }

  disconnect() {
    this.disconnected = true;
  }

  trigger() {
    this.callback([], this);
  }
}

rectangle = { width: 1280.9, height: 577.8 };
let ratio = 1;
let dispatched = 0;
const observed = [];
const bridge = observeCanvasResize(canvas, {
  ResizeObserverClass: FakeResizeObserver,
  dispatchResize: () => {
    dispatched += 1;
  },
  onResize: (size) => observed.push(size),
  devicePixelRatio: () => ratio,
});
assert.equal(bridge.supported, true);
assert.deepEqual(observer.observed, [canvas]);
checks += 2;

observer.trigger();
assert.equal(dispatched, 0);
assert.deepEqual(observed, []);
checks += 2;

rectangle = { width: 960.9, height: 540.4 };
observer.trigger();
assert.equal(dispatched, 1);
assert.deepEqual(observed.at(-1), {
  cssWidth: 960,
  cssHeight: 540,
  devicePixelRatio: 1,
});
assert.deepEqual(bridge.current(), observed.at(-1));
checks += 3;

rectangle = { width: 960.2, height: 540.9 };
observer.trigger();
assert.equal(dispatched, 1);
checks += 1;

ratio = 2;
observer.trigger();
assert.equal(dispatched, 2);
assert.equal(observed.at(-1).devicePixelRatio, 2);
checks += 2;

bridge.disconnect();
assert.equal(observer.disconnected, true);
checks += 1;

const fallback = observeCanvasResize(canvas, {
  ResizeObserverClass: undefined,
  devicePixelRatio: () => 2,
});
assert.equal(fallback.supported, false);
assert.deepEqual(fallback.current(), measureCanvas(canvas, 2));
fallback.disconnect();
checks += 2;

process.stdout.write(`${JSON.stringify({ passed: true, checks })}\n`);
