// Keep browser tests pinned to the repository manifest, with all runtime files in .e2e/.
import { mkdirSync, copyFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
const root = resolve(import.meta.dirname, "..");
const work = resolve(root, ".e2e");
mkdirSync(work, { recursive: true });
mkdirSync(resolve(work, "tmp"), { recursive: true });
copyFileSync(
  resolve(root, "test/e2e.package.json"),
  resolve(work, "package.json"),
);
const env = {
  ...process.env,
  npm_config_cache: resolve(work, "npm-cache"),
  TMPDIR: resolve(work, "tmp"),
  PLAYWRIGHT_BROWSERS_PATH: resolve(work, "browsers"),
};
for (const [cmd, args] of [
  ["npm", ["install", "--prefix", work, "--no-audit", "--no-fund"]],
  [
    process.execPath,
    [resolve(work, "node_modules/playwright/cli.js"), "install", "chromium"],
  ],
]) {
  const result = spawnSync(cmd, args, { cwd: root, env, stdio: "inherit" });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
