#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const wheel = fileURLToPath(new URL("../python/kitctl-0.0.1-py3-none-any.whl", import.meta.url));
if (!existsSync(wheel)) {
  console.error("kitctl: bundled Python wheel is missing; reinstall the npm package.");
  process.exit(1);
}
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
