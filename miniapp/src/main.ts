import { createSSRApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { installSessionHandling } from "./utils/session";

export function createApp() {
  const app = createSSRApp(App);
  const pinia = createPinia();
  app.use(pinia);
  installSessionHandling(pinia);
  return {
    app,
  };
}
