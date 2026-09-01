// SPDX-License-Identifier: GPL-2.0-or-later
//
// The browser owns the canvas CSS box, while SDL owns its backing store and
// the engine's video mode. SDL's Emscripten driver already translates a window
// `resize` event into SDL_WINDOWEVENT_RESIZED. A ResizeObserver closes the one
// missing browser case: CSS and element-fullscreen changes that resize the
// canvas without resizing the browser window itself.

export const MINIMUM_RENDER_WIDTH = 320;
export const MINIMUM_RENDER_HEIGHT = 240;

function finiteDevicePixelRatio(value) {
  return Number.isFinite(value) && value > 0 ? value : 1;
}

export function measureCanvas(canvas, devicePixelRatio = globalThis.devicePixelRatio) {
  if (!canvas || typeof canvas.getBoundingClientRect !== "function") {
    throw new TypeError("canvas must provide getBoundingClientRect()");
  }
  const rect = canvas.getBoundingClientRect();
  return {
    cssWidth: Math.max(MINIMUM_RENDER_WIDTH, Math.floor(rect.width)),
    cssHeight: Math.max(MINIMUM_RENDER_HEIGHT, Math.floor(rect.height)),
    devicePixelRatio: finiteDevicePixelRatio(devicePixelRatio),
  };
}

export function renderSizeArguments(size) {
  return [
    "+set",
    "r_mode",
    "-1",
    "+set",
    "r_customwidth",
    String(size.cssWidth),
    "+set",
    "r_customheight",
    String(size.cssHeight),
  ];
}

export function observeCanvasResize(
  canvas,
  {
    ResizeObserverClass = globalThis.ResizeObserver,
    dispatchResize = () => globalThis.dispatchEvent(new Event("resize")),
    onResize = () => {},
    devicePixelRatio = () => globalThis.devicePixelRatio,
  } = {},
) {
  let current = measureCanvas(canvas, devicePixelRatio());
  if (typeof ResizeObserverClass !== "function") {
    return {
      supported: false,
      current: () => ({ ...current }),
      disconnect: () => {},
    };
  }

  const check = () => {
    const next = measureCanvas(canvas, devicePixelRatio());
    if (
      next.cssWidth === current.cssWidth &&
      next.cssHeight === current.cssHeight &&
      next.devicePixelRatio === current.devicePixelRatio
    ) {
      return false;
    }
    current = next;
    onResize({ ...next });
    // SDL's registered Emscripten resize callback reads the live CSS box,
    // updates the canvas backing store and queues SDL_WINDOWEVENT_RESIZED.
    dispatchResize();
    return true;
  };

  const observer = new ResizeObserverClass(check);
  observer.observe(canvas);
  return {
    supported: true,
    current: () => ({ ...current }),
    check,
    disconnect: () => observer.disconnect(),
  };
}
