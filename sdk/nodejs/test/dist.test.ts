// Smoke tests against the BUILT package (dist/), loaded through its
// package.json "exports" map exactly as users load it: once via require()
// from a CommonJS app, once via import from an ES module app.
// Run `npm run build` first; skipped when dist/ is missing.

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, symlinkSync, cpSync } from "node:fs";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const built = existsSync(path.join(root, "dist/index.js")) && existsSync(path.join(root, "dist/index.mjs"));

let appDir = "";
let sinkUrl = "";
let received: unknown[] = [];
let server: http.Server;

beforeAll(async () => {
  if (!built) return;
  // A throwaway "app" whose node_modules link to this package and its peers.
  appDir = mkdtempSync(path.join(tmpdir(), "opentelm-app-"));
  mkdirSync(path.join(appDir, "node_modules/@anthropic-ai"), { recursive: true });
  symlinkSync(root, path.join(appDir, "node_modules/opentelm-node"), "dir");
  symlinkSync(path.join(root, "node_modules/openai"), path.join(appDir, "node_modules/openai"), "dir");
  symlinkSync(path.join(root, "node_modules/@anthropic-ai/sdk"), path.join(appDir, "node_modules/@anthropic-ai/sdk"), "dir");
  cpSync(path.join(root, "test/fixtures"), appDir, { recursive: true });

  server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (d) => { body += d; });
    req.on("end", () => { received.push(...JSON.parse(body).events); res.writeHead(202); res.end("{}"); });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  sinkUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(() => new Promise<void>((r) => (server ? server.close(() => r()) : r())));

function run(file: string): Promise<{ code: number | null; stderr: string }> {
  return new Promise((resolve) => {
    // --no-deprecation: older openai releases trigger Node's punycode warning.
    const child = spawn(process.execPath, ["--no-deprecation", file],
                        { cwd: appDir, env: { ...process.env, SINK_URL: sinkUrl } });
    let stderr = "";
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("exit", (code) => resolve({ code, stderr }));
  });
}

describe.runIf(built)("built package", () => {
  it.each(["app.cjs", "app.mjs"])("%s: all calls are traced and flushed on exit", async (file) => {
    received = [];
    const { code, stderr } = await run(file);
    expect(stderr).toBe("");
    expect(code).toBe(0);
    const providers = (received as Array<{ provider: string }>).map((e) => e.provider).sort();
    expect(providers).toEqual(["anthropic", "openai", "openai"]);
  });

  it("the app still exits on the first SIGINT", async () => {
    const child = spawn(process.execPath, ["idle.mjs"], { cwd: appDir });
    await new Promise<void>((r) => child.stdout.once("data", () => r()));
    const t0 = Date.now();
    child.kill("SIGINT");
    const signal = await new Promise((r) => child.on("exit", (_code, sig) => r(sig)));
    expect(signal).toBe("SIGINT");
    expect(Date.now() - t0).toBeLessThan(2_000);
  });
});
