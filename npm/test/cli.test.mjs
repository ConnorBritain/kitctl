import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import test from "node:test";

const bin = fileURLToPath(new URL("../bin/kitctl.mjs", import.meta.url));
test("bundled wheel works without a PyPI release and forwards arguments", () => {
  const root = mkdtempSync(path.join(tmpdir(), "kitctl-npm-"));
  const env = { ...process.env, HOME: root, USERPROFILE: root,
    CODEX_HOME: path.join(root, ".codex"), CLAUDE_CONFIG_DIR: path.join(root, ".claude") };
  try {
    const version = spawnSync(process.execPath, [bin, "--version"], { env, encoding: "utf8" });
    assert.equal(version.status, 0, version.stderr);
    assert.match(version.stdout, /^kitctl 0\.0\.1\r?\n$/);
    const repo = path.join(root, "repo with spaces");
    mkdirSync(repo);
    writeFileSync(path.join(repo, "AGENTS.md"), "Fixture instructions");
    const scan = spawnSync(process.execPath, [bin, "inventory", "--root", repo, "--json"], {
      env, encoding: "utf8",
    });
    assert.equal(scan.status, 0, scan.stderr);
    assert.equal(JSON.parse(scan.stdout).entries[0].name, "AGENTS.md");
    const failure = spawnSync(process.execPath, [bin, "inventory", "--root", path.join(root, "missing")], {
      env, encoding: "utf8",
    });
    assert.equal(failure.status, 2);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
