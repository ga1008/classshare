import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  root: fileURLToPath(new URL("../", import.meta.url)),
  resolve: { alias: {
    vue: fileURLToPath(new URL("./node_modules/vue/dist/vue.runtime.esm-bundler.js", import.meta.url)),
    pinia: fileURLToPath(new URL("./node_modules/pinia/dist/pinia.mjs", import.meta.url)),
  } },
  test: { include: ["miniapp/tests/*.test.ts"], environment: "node" },
});
