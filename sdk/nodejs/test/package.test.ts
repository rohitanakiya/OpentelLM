import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { SDK_VERSION } from "../src/version";

it("src/version.ts matches package.json", () => {
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  expect(SDK_VERSION).toBe(pkg.version);
});
