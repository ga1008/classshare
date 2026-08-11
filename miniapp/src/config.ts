/**
 * 全局配置。
 *
 * PROD_API_BASE：备案域名 + HTTPS 就绪后替换（小程序合法域名不接受 IP）。
 * 开发期在微信开发者工具勾选"不校验合法域名"，直连本机后端。
 */
// 本机局域网 IP：开发者工具与同 Wi-Fi 真机都能直连（后端绑 0.0.0.0）。
// 换网络后 ipconfig 查新 IP 更新这里。
const DEV_API_BASE = "http://192.168.5.19:8000";
const PROD_API_BASE = "https://guardianangel.net.cn";

export const API_BASE: string = import.meta.env.DEV ? DEV_API_BASE : PROD_API_BASE;

export const TOKEN_STORAGE_KEY = "lanshareMpToken";
export const TIP_SEEN_STORAGE_KEY = "lanshareLifeTipSeen";
export const TIP_SEEN_LIMIT = 20;
