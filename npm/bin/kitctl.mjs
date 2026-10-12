#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const bundle = fileURLToPath(new URL("../python/", import.meta.url));
let wheels = [];
try {
  wheels = readdirSync(bundle).filter((name) => /^kitctl-.+-py3-none-any\.whl$/.test(name));
} catch {}
if (wheels.length !== 1) {
  console.error("kitctl: bundled Python wheel is missing; reinstall the npm package.");
  process.exit(1);
}
const wheel = path.join(bundle, wheels[0]);
const result = spawnSync("uvx", ["--from", wheel, "kitctl", ...process.argv.slice(2)], {
  stdio: "inherit",
  shell: false,
});
if (result.error) {
  console.error(result.error.code === "ENOENT"
    ? "kitctl requires uv. Install it from https://docs.astral.sh/uv/getting-started/installation/ then retry."
    : `kitctl: ${result.error.message}`);
  process.exit(1);
}
if (result.signal) {
  process.kill(process.pid, result.signal);
} else {
  process.exit(result.status ?? 1);
}
