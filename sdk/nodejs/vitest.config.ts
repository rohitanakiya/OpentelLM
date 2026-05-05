import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
    testTimeout: 10_000,
    pool: "forks",  // patch tests mutate Module._load — process isolation matters
  },
});
