// SPDX-License-Identifier: GPL-2.0-or-later
//
// Small, DOM-free notification and terminal-settlement primitive for the
// public arena-web host API. Keeping it separate makes the exact subscription
// and exactly-once promises testable without a browser or engine build.

export class HostLifecycleError extends Error {
  constructor(message) {
    super(message);
    this.name = "HostLifecycleError";
  }
}

function terminalResult(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new HostLifecycleError("terminal result must be an object");
  }
  if (!["exited", "failed"].includes(value.status)) {
    throw new HostLifecycleError("terminal status must be 'exited' or 'failed'");
  }
  if (value.exitCode !== null && !Number.isInteger(value.exitCode)) {
    throw new HostLifecycleError("terminal exitCode must be an integer or null");
  }
  if (typeof value.reason !== "string" || value.reason === "") {
    throw new HostLifecycleError("terminal reason must be a non-empty string");
  }
  return Object.freeze({
    status: value.status,
    exitCode: value.exitCode,
    reason: value.reason,
  });
}

export function createHostLifecycle(snapshot, { onListenerError = () => {} } = {}) {
  if (typeof snapshot !== "function") {
    throw new HostLifecycleError("snapshot provider must be a function");
  }
  if (typeof onListenerError !== "function") {
    throw new HostLifecycleError("listener error handler must be a function");
  }

  const listeners = new Set();
  let terminal = null;
  let resolveSettlement;
  const settlement = new Promise((resolve) => {
    resolveSettlement = resolve;
  });

  const deliver = (listener, value) => {
    try {
      listener(value);
    } catch (error) {
      onListenerError(error);
    }
  };

  return Object.freeze({
    subscribe(listener) {
      if (typeof listener !== "function") {
        throw new HostLifecycleError("subscriber must be a function");
      }
      listeners.add(listener);
      // Registration always includes the current state synchronously. A
      // consumer never has to race its first read against a later event.
      deliver(listener, snapshot());
      let active = true;
      return () => {
        if (active) {
          active = false;
          listeners.delete(listener);
        }
      };
    },

    publish() {
      const value = snapshot();
      for (const listener of [...listeners]) {
        deliver(listener, value);
      }
    },

    settle(value, commit = () => {}) {
      if (terminal !== null) {
        return false;
      }
      if (typeof commit !== "function") {
        throw new HostLifecycleError("terminal commit must be a function");
      }
      // Latch the immutable terminal result before mutating and publishing the
      // public snapshot. A subscriber may synchronously call back into stop()
      // from this notification; terminal() must already be authoritative then.
      terminal = terminalResult(value);
      commit(terminal);
      resolveSettlement(terminal);
      const current = snapshot();
      for (const listener of [...listeners]) {
        deliver(listener, current);
      }
      return true;
    },

    whenSettled() {
      return settlement;
    },

    terminal() {
      return terminal;
    },
  });
}
