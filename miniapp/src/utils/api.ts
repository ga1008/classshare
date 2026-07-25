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

/** 上传单个文件到后端 multipart Form 端点（自动带 bearer，解析 JSON 响应）。 */
export function uploadFile<T>(options: {
  path: string;
  filePath: string;
  name?: string;
  formData?: Record<string, string>;
}): Promise<T> {
  const { path, filePath, name = "files", formData = {} } = options;
  const header: Record<string, string> = {};
  const token = getStoredToken();
  if (token) {
    header.Authorization = `Bearer ${token}`;
  }
  return new Promise<T>((resolve, reject) => {
    uni.uploadFile({
      url: `${API_BASE}${path}`,
      filePath,
      name,
      formData,
      header,
      timeout: 60000,
      success: (res) => {
        const status = res.statusCode ?? 0;
        let body: unknown = null;
        try {
          body = JSON.parse(res.data || "null");
        } catch {
          body = null;
        }
        if (status < 200 || status >= 300) {
          reject(new ApiError(extractDetail(body, `上传失败（${status}）`), status));
          return;
        }
        resolve(body as T);
      },
      fail: () => reject(new ApiError("上传失败，请检查网络后重试。", 0)),
    });
  });
}

export function request<T>(options: {
  path: string;
  method?: "GET" | "POST";
  data?: Record<string, unknown>;
  auth?: boolean;
  /** true → application/x-www-form-urlencoded（对接后端 Form 端点，如草稿/提交） */
  form?: boolean;
}): Promise<T> {
  const { path, method = "GET", data, auth = true, form = false } = options;
  const header: Record<string, string> = {
    "content-type": form ? "application/x-www-form-urlencoded" : "application/json",
  };
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
