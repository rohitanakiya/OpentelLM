import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["cjs", "esm"],
  dts: true,
  clean: true,
  target: "node18",
  // `import.meta.url` (used to locate the user's openai / anthropic) also
  // works in the CommonJS build.
  shims: true,
  esbuildOptions(options) {
    // Keep `import()` as a real dynamic import in the CommonJS build too.
    // esbuild would otherwise turn it into require(), which loads the CJS
    // copy of openai, never the ES module copy that `import` users get.
    options.supported = { ...options.supported, "dynamic-import": true };
  },
});
