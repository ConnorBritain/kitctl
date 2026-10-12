#!/usr/bin/env node
// kitctl npm launcher: runs the Python wheel bundled in this package.
// 1. uv on PATH  -> uvx --from <wheel> kitctl ...
// 2. Python 3.11+ -> import kitctl straight from the wheel (pure Python, zero deps)
// 3. otherwise    -> explain how to install.
// Hooks should call an installed `kitctl` (uv tool install kitctl / pipx install kitctl):
// this launcher's Node -> Python startup is too slow for session-start hooks.
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const TOO_OLD = 3; // exit code from the bootstrap when Python is older than 3.11
const args = process.argv.slice(2);
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

function run(command, commandArgs, env = process.env) {
  return spawnSync(command, commandArgs, { stdio: "inherit", shell: false, env });
}

function finish(result) {
  if (result.signal) process.kill(process.pid, result.signal);
  process.exit(result.status ?? 1);
}

if (!process.env.KITCTL_LAUNCHER_NO_UV) {
  const viaUv = run("uvx", ["--from", wheel, "kitctl", ...args]);
  if (!viaUv.error) finish(viaUv);
  if (viaUv.error.code !== "ENOENT") {
    console.error(`kitctl: ${viaUv.error.message}`);
    process.exit(1);
  }
}

const bootstrap = [
  "import sys",
  `sys.exit(${TOO_OLD}) if sys.version_info < (3, 11) else None`,
  "sys.path.insert(0, sys.argv[1])",
  "sys.argv = ['kitctl'] + sys.argv[2:]",
  "import runpy",
  "runpy.run_module('kitctl', run_name='__main__', alter_sys=True)",
].join("\n");
const candidates = process.platform === "win32"
  ? [["py", ["-3"]], ["python", []], ["python3", []]]
  : [["python3", []], ["python", []]];
for (const [command, prefix] of candidates) {
  const result = run(command, [...prefix, "-c", bootstrap, wheel, ...args]);
  if (result.error) continue; // not installed
  if (result.status === TOO_OLD) continue; // too old; try the next one
  if (process.platform === "win32" && result.status === 9009) continue; // Store alias stub
  finish(result);
}

console.error(
  "kitctl needs uv or Python 3.11+.\n" +
  "  Recommended: install uv (https://docs.astral.sh/uv/getting-started/installation/),\n" +
  "  then `uv tool install kitctl`. Or install Python 3.11+ and run `pipx install kitctl`."
);
process.exit(1);
