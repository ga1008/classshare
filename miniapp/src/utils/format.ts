/** 日期/截止时间的展示格式化（客户端本地化，服务器只传 ISO）。 */

function parse(iso: string): Date | null {
  if (!iso) return null;
  const normalized = iso.replace(" ", "T");
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "8月30日 23:59"；无截止返回 "长期有效"。 */
export function formatDueLabel(iso: string): string {
  const date = parse(iso);
  if (!date) return "长期有效";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 相对截止："今天 23:59 截止" / "3天后截止" / "已截止"。 */
export function relativeDueLabel(iso: string): string {
  const date = parse(iso);
  if (!date) return "";
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  if (diffMs <= 0) return "已截止";
  const diffHours = diffMs / 3_600_000;
  if (diffHours < 1) return `${Math.max(1, Math.round(diffMs / 60_000))}分钟后截止`;
  if (diffHours < 24) return `${Math.round(diffHours)}小时后截止`;
  return `${Math.round(diffHours / 24)}天后截止`;
}
