import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { include: ["miniapp/tests/assessment-contracts.test.ts"], environment: "node" },
});
