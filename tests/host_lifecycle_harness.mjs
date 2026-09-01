// SPDX-License-Identifier: GPL-2.0-or-later

import assert from "node:assert/strict";
import { createHostLifecycle, HostLifecycleError } from "../arena/host-lifecycle.js";

let checks = 0;
let state = { status: "starting", sequence: 0 };
const listenerErrors = [];
const lifecycle = createHostLifecycle(
  () => Object.freeze({ ...state }),
  { onListenerError: (error) => listenerErrors.push(error.message) },
);

const first = [];
const unsubscribe = lifecycle.subscribe((snapshot) => first.push(snapshot));
assert.deepEqual(first, [{ status: "starting", sequence: 0 }]);
checks += 1;

state = { status: "ready", sequence: 1 };
lifecycle.publish();
assert.deepEqual(first.at(-1), { status: "ready", sequence: 1 });
checks += 1;

let throwingCalls = 0;
const unsubscribeThrowing = lifecycle.subscribe(() => {
  throwingCalls += 1;
  throw new Error("consumer failure");
});
assert.equal(throwingCalls, 1);
assert.deepEqual(listenerErrors, ["consumer failure"]);
checks += 2;

state = { status: "booting", sequence: 2 };
lifecycle.publish();
assert.equal(throwingCalls, 2);
assert.deepEqual(first.at(-1), { status: "booting", sequence: 2 });
checks += 2;

unsubscribe();
unsubscribe();
unsubscribeThrowing();
state = { status: "running", sequence: 3 };
lifecycle.publish();
assert.equal(first.length, 3);
assert.equal(throwingCalls, 2);
checks += 2;

const settlementA = lifecycle.whenSettled();
const settlementB = lifecycle.whenSettled();
assert.equal(settlementA, settlementB);
checks += 1;
assert.equal(
  lifecycle.settle({ status: "exited", exitCode: 0, reason: "host_stop" }),
  true,
);
assert.equal(
  lifecycle.settle({ status: "failed", exitCode: null, reason: "late_abort" }),
  false,
);
checks += 2;
assert.deepEqual(await settlementA, {
  status: "exited",
  exitCode: 0,
  reason: "host_stop",
});
assert.deepEqual(lifecycle.terminal(), await settlementB);
assert.equal(Object.isFrozen(await settlementB), true);
checks += 3;

assert.throws(() => lifecycle.subscribe(null), HostLifecycleError);
assert.throws(
  () => createHostLifecycle(() => ({})).settle({ status: "stopped", exitCode: 0, reason: "x" }),
  HostLifecycleError,
);
assert.throws(
  () => createHostLifecycle(() => ({})).settle({ status: "failed", exitCode: "1", reason: "x" }),
  HostLifecycleError,
);
checks += 3;

process.stdout.write(`${JSON.stringify({ passed: true, checks })}\n`);
