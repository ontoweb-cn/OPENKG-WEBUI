#!/usr/bin/env node
/**
 * Dev-server entrypoint that pins the V8 heap ceiling of the process that
 * actually holds the memory.
 *
 * `next dev` does not render in the process you launch — it spawns a separate
 * render worker, and V8 flags in argv are not inherited by child processes. So
 * the long-standing `node --max-old-space-size=4096 .../next dev` never reached
 * the worker: Next reads the ceiling from NODE_OPTIONS
 * (`getMaxOldSpaceSize()`), finds nothing, and substitutes 50% of total RAM
 * with no upper bound (`next dev` CLI, guarded by NEXT_DISABLE_MEM_OVERRIDE).
 * On a 24GB machine that is a 12GB heap, which V8 will genuinely try to fill:
 * an hour-long session died in `Ineffective mark-compacts near heap limit`
 * after spending 63 seconds inside a single mark-compact, with the machine
 * swapping long before that.
 *
 * Setting the ceiling here fixes both halves — the worker inherits NODE_OPTIONS,
 * and Next stops overriding it. Keep Next's 50% rule so small machines are not
 * handed a heap larger than their RAM, but cap it: past a few GB the extra
 * headroom buys a slower death, not a working dev server. An explicit
 * NODE_OPTIONS ceiling from the developer always wins.
 */

import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HEAP_CEILING_MB = 4096;
const WEB_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const NEXT_BIN = path.join(WEB_DIR, "node_modules", "next", "dist", "bin", "next");

/** Memory this process may actually use, in MB — cgroup limit included so the
 *  container dev stage sizes to its own budget rather than the host's RAM. */
function memoryBudgetMB() {
  let bytes = os.totalmem();
  try {
    const limit = Number(readFileSync("/sys/fs/cgroup/memory.max", "utf8").trim());
    if (Number.isFinite(limit) && limit > 0) bytes = Math.min(bytes, limit);
  } catch {
    // No cgroup v2 limit (macOS, Windows, unconstrained container).
  }
  return Math.floor(bytes / 1024 / 1024);
}

const inherited = process.env.NODE_OPTIONS ?? "";
const nodeOptions = /--max[-_]old[-_]space[-_]size/.test(inherited)
  ? inherited
  : `${inherited} --max-old-space-size=${Math.min(
      HEAP_CEILING_MB,
      Math.floor(memoryBudgetMB() * 0.5),
    )}`.trim();

// A bare `npm run dev` must land on the port the app declares (8092, matching
// system.json `frontend_port`) instead of Next's implicit 3000, so both entry
// points agree. An explicit --port/-p from the caller always wins — the launcher
// passes one, and it must keep overriding this default.
const DEFAULT_PORT = 8092;
const forwarded = process.argv.slice(2);
const portGiven = forwarded.some((arg) => /^--port(=|$)|^-p(=|$|\d)/.test(arg));
const devArgs = portGiven ? forwarded : [...forwarded, "--port", String(DEFAULT_PORT)];

const child = spawn(process.execPath, [NEXT_BIN, "dev", ...devArgs], {
  cwd: WEB_DIR,
  stdio: "inherit",
  env: { ...process.env, NODE_OPTIONS: nodeOptions },
});

// Let Next own the terminal: forward the signals it already handles (lock file
// cleanup, worker teardown) instead of dying first and orphaning the worker.
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});
