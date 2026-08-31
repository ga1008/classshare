/**
 * 订阅消息授权：模板 ID 从服务端取（/api/mp/subscribe/config），
 * 用户点"允许"后把 accept 的 key 上报（/report）给额度台账 +1。
 *
 * 微信要求 requestSubscribeMessage 必须在用户点击手势内调用——
 * 配置需提前预取（prefetchSubscribeConfig），保证手势内零网络等待。
 */
import { request } from "./api";

type TemplateKey = "deadline" | "nudge" | "graded";

let templateConfig: Record<string, string> | null = null;

export async function prefetchSubscribeConfig(): Promise<void> {
  if (templateConfig) return;
  try {
    const data = await request<{ templates: Record<string, string> }>({
      path: "/api/mp/subscribe/config",
    });
    templateConfig = data.templates || {};
  } catch {
    /* 配置拉取失败不影响主流程，下次再试 */
  }
}

/**
 * 在用户手势内调用。弹微信授权框，把允许的模板上报服务端。
 * 静默失败（用户拒绝/勾了不再询问/平台不支持都不打扰主流程）。
 */
export function requestSubscribe(keys: TemplateKey[]): void {
  const config = templateConfig;
  if (!config) {
    void prefetchSubscribeConfig();
    return;
  }
  const ids = keys.map((key) => config[key]).filter(Boolean);
  if (!ids.length) return;
  uni.requestSubscribeMessage({
    tmplIds: ids,
    success: (res) => {
      const results = res as unknown as Record<string, string>;
      const accepted = keys.filter((key) => results[config[key]] === "accept");
      if (!accepted.length) return;
      void request({
        path: "/api/mp/subscribe/report",
        method: "POST",
        data: { accepted },
      }).catch(() => {
        /* 上报失败下次授权补记 */
      });
    },
    fail: () => {
      /* 用户环境不支持或拒绝，静默 */
    },
  });
}
