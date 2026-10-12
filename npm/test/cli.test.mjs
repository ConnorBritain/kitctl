import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import test from "node:test";

const bin = fileURLToPath(new URL("../bin/kitctl.mjs", import.meta.url));
const windows = process.platform === "win32";

function sandbox() {
  const root = mkdtempSync(path.join(tmpdir(), "kitctl-npm-"));
  const env = { ...process.env, HOME: root, USERPROFILE: root,
    CODEX_HOME: path.join(root, ".codex"), CLAUDE_CONFIG_DIR: path.join(root, ".claude") };
  return { root, env };
}

function which(command) {
  const finder = windows ? "where" : "which";
  const found = spawnSync(finder, [command], { encoding: "utf8" });
  return found.status === 0 ? found.stdout.split(/\r?\n/)[0].trim() : null;
}

function smoke(env, root) {
  const version = spawnSync(process.execPath, [bin, "--version"], { env, encoding: "utf8" });
  assert.equal(version.status, 0, version.stderr);
  assert.match(version.stdout, /^kitctl \d+\.\d+\.\d+\S*\r?\n$/);
  const repo = path.join(root, "repo with spaces");
  mkdirSync(repo, { recursive: true });
  writeFileSync(path.join(repo, "AGENTS.md"), "Fixture instructions");
  const scan = spawnSync(process.execPath, [bin, "inventory", "--root", repo, "--json"], {
    env, encoding: "utf8",
  });
  assert.equal(scan.status, 0, scan.stderr);
  assert.equal(JSON.parse(scan.stdout).entries.find((e) => e.kind === "instructions").name, "AGENTS.md");
  const failure = spawnSync(process.execPath, [bin, "inventory", "--root", path.join(root, "missing")], {
    env, encoding: "utf8",
  });
  assert.equal(failure.status, 2);
}

test("path 1: uvx runs the bundled wheel and forwards arguments", { skip: !which("uvx") && "uv not installed" }, () => {
  const { root, env } = sandbox();
  try { smoke(env, root); } finally { rmSync(root, { recursive: true, force: true }); }
});

test("path 2: Python 3.11+ imports kitctl from the wheel when uv is absent", (t) => {
  const python = process.env.KITCTL_TEST_PYTHON || which(windows ? "python" : "python3");
  if (!python) return t.skip("no python found");
  const { root, env } = sandbox();
  try {
    if (windows) {
      // Node won't spawn .cmd shims without a shell; expose the real interpreter's folder.
      env.PATH = path.dirname(python);
    } else {
      const shims = path.join(root, "bin");
      mkdirSync(shims);
      symlinkSync(python, path.join(shims, "python3"));
      env.PATH = shims;
    }
    env.KITCTL_LAUNCHER_NO_UV = "1";
    smoke(env, root);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("path 3: without uv or Python, explains how to install", () => {
  const { root, env } = sandbox();
  try {
    env.PATH = path.join(root, "empty");
    env.KITCTL_LAUNCHER_NO_UV = "1";
    const result = spawnSync(process.execPath, [bin, "--version"], { env, encoding: "utf8" });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /needs uv or Python 3\.11\+/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
