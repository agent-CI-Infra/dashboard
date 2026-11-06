const { defineConfig } = require("../.e2e/node_modules/@playwright/test");
const { resolve } = require("node:path");
const { spawnSync } = require("node:child_process");
const { mkdirSync } = require("node:fs");
const root = resolve(__dirname, "..");
for (const name of ["tmp", "cache", "config"])
  mkdirSync(resolve(root, ".e2e", name), { recursive: true });
process.env.PLAYWRIGHT_BROWSERS_PATH = resolve(root, ".e2e/browsers");
process.env.TMPDIR = resolve(root, ".e2e/tmp");
process.env.XDG_CACHE_HOME = resolve(root, ".e2e/cache");
process.env.XDG_CONFIG_HOME = resolve(root, ".e2e/config");
if (
  spawnSync("python3", ["test/prepare-site.py"], {
    cwd: root,
    stdio: "inherit",
  }).status
)
  throw new Error("site preparation failed");
module.exports = defineConfig({
  testDir: __dirname,
  testMatch: "journey-*.spec.cjs",
  workers: 1,
  outputDir: resolve(root, ".e2e/results"),
  reporter: [
    ["list"],
    ["json", { outputFile: resolve(root, ".e2e/results.json") }],
  ],
  use: {
    baseURL: "http://127.0.0.1:18890",
    viewport: { width: 1440, height: 1000 },
    timezoneId: "Asia/Shanghai",
    screenshot: "only-on-failure",
  },
  webServer: {
    command:
      "python3 -m http.server 18890 --bind 127.0.0.1 --directory .e2e/site",
    cwd: root,
    url: "http://127.0.0.1:18890/github-status-board/",
    reuseExistingServer: false,
    stdout: "ignore",
    stderr: "ignore",
  },
});
