/**
 * 统一请求封装：token 注入、{success,data,error} 信封解包、401 兜底。
 */
import { API_BASE, TOKEN_STORAGE_KEY } from "../config";

interface ApiEnvelope<T> {
  success: boolean;
  data: T;
  error: string | null;
}

export class ApiError extends Error {
  statusCode: number;

  constructor(message: string, statusCode: number) {
    super(message);
    this.statusCode = statusCode;
  }
}

export function getStoredToken(): string {
  try {
    return (uni.getStorageSync(TOKEN_STORAGE_KEY) as string) || "";
  } catch {
    return "";
  }
}

export function setStoredToken(token: string): void {
  try {
    if (token) {
      uni.setStorageSync(TOKEN_STORAGE_KEY, token);
    } else {
      uni.removeStorageSync(TOKEN_STORAGE_KEY);
    }
  } catch {
    /* storage 失败不阻断流程 */
  }
}

function extractDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    if (typeof record.detail === "string" && record.detail) {
      return record.detail;
    }
    if (typeof record.error === "string" && record.error) {
      return record.error;
    }
  }
  return fallback;
}

export function request<T>(options: {
  path: string;
  method?: "GET" | "POST";
  data?: Record<string, unknown>;
  auth?: boolean;
}): Promise<T> {
  const { path, method = "GET", data, auth = true } = options;
  const header: Record<string, string> = { "content-type": "application/json" };
  if (auth) {
    const token = getStoredToken();
    if (token) {
      header.Authorization = `Bearer ${token}`;
    }
  }
  return new Promise<T>((resolve, reject) => {
    uni.request({
      url: `${API_BASE}${path}`,
      method,
      data,
      header,
      timeout: 15000,
      success: (res) => {
        const status = res.statusCode ?? 0;
        if (status === 401) {
          setStoredToken("");
          reject(new ApiError(extractDetail(res.data, "登录已过期"), 401));
          return;
        }
        if (status < 200 || status >= 300) {
          reject(new ApiError(extractDetail(res.data, `请求失败（${status}）`), status));
          return;
        }
        const body = res.data as ApiEnvelope<T>;
        if (body && typeof body === "object" && "success" in body) {
          if (body.success) {
            resolve(body.data);
          } else {
            reject(new ApiError(body.error || "请求失败", status));
          }
          return;
        }
        resolve(res.data as T);
      },
      fail: () => {
        reject(new ApiError("网络异常，请检查网络后重试。", 0));
      },
    });
  });
}
