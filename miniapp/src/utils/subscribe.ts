/**
 * 订阅消息授权：模板 ID 与本人剩余额度从服务端取（/api/mp/subscribe/config），
 * 用户点"允许"后把 accept 的 key 上报（/report，带一次性 report_id 幂等）。
 *
 * 微信的一次性订阅是"按模板计次"的：每允许一次，该模板多一条下发机会，
 * 与具体作业无关。所以页面展示的是"剩余 N 次"，而不是"已为某作业订阅"。
 *
 * 微信要求 requestSubscribeMessage 必须在用户点击手势内调用——
 * 配置需提前预取（prefetchSubscribeConfig），保证手势内零网络等待。
 */
import { request } from "./api";

export type TemplateKey = "deadline" | "nudge" | "graded";
export type SubscribeBalances = Partial<Record<TemplateKey, number>>;
export type SubscribeOutcome = "asked" | "no_config" | "no_template" | "unsupported";

let templateConfig: Record<string, string> | null = null;
let balances: SubscribeBalances = {};
const listeners = new Set<(value: SubscribeBalances) => void>();

function publish(next: SubscribeBalances | undefined): void {
  if (!next) return;
  balances = { ...next };
  listeners.forEach((listener) => listener(balances));
}

/** 订阅本人剩余额度变化；返回取消函数。立即回放一次当前值。 */
export function onSubscribeBalances(listener: (value: SubscribeBalances) => void): () => void {
  listeners.add(listener);
  listener(balances);
  return () => {
    listeners.delete(listener);
  };
}

export async function prefetchSubscribeConfig(force = false): Promise<void> {
  if (templateConfig && !force) return;
  try {
    const data = await request<{ templates: Record<string, string>; balances?: SubscribeBalances }>({
      path: "/api/mp/subscribe/config",
    });
    templateConfig = data.templates || {};
    publish(data.balances);
  } catch {
    /* 配置拉取失败不影响主流程，下次再试 */
  }
}

function newReportId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 在用户手势内调用（同步发起）。弹微信授权框，把允许的模板上报服务端。
 * 用户拒绝/勾了不再询问/平台不支持都不打扰主流程。
 *
 * 配置未预取时不静默放弃：先拉配置再弹框（可能已脱离手势，微信会
 * 拒绝，但比"永远不问"好），返回值告诉页面与测试走了哪条路。
 */
export function requestSubscribe(keys: TemplateKey[]): Promise<SubscribeOutcome> {
  if (templateConfig) return Promise.resolve(askWithConfig(templateConfig, keys));
  return prefetchSubscribeConfig().then(() =>
    templateConfig ? askWithConfig(templateConfig, keys) : "no_config",
  );
}

function askWithConfig(config: Record<string, string>, keys: TemplateKey[]): SubscribeOutcome {
  const ids = keys.map((key) => config[key]).filter(Boolean);
  if (!ids.length) return "no_template";
  const reportId = newReportId();
  try {
    uni.requestSubscribeMessage({
      tmplIds: ids,
      success: (res) => {
        const results = res as unknown as Record<string, string>;
        const accepted = keys.filter((key) => results[config[key]] === "accept");
        if (!accepted.length) return;
        void request<{ balances?: SubscribeBalances }>({
          path: "/api/mp/subscribe/report",
          method: "POST",
          data: { accepted, report_id: reportId },
        }).then((data) => publish(data.balances)).catch(() => {
          /* 上报失败：同一 report_id 不会重复计次，下次授权补记 */
        });
      },
      fail: () => {
        /* 用户环境不支持或拒绝，静默 */
      },
    });
  } catch {
    return "unsupported";
  }
  return "asked";
}
