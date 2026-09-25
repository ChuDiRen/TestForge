/** 后端统一封套 {code, message, data} + 认证 token 管理 */

export interface Envelope<T = unknown> {
  code: number;
  message: string;
  data: T;
}

const TOKEN_KEY = "tf_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class AuthError extends Error {
  constructor(msg = "未登录或登录已过期") {
    super(msg);
  }
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(path, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string>) } });
  if (resp.status === 401) {
    setToken("");
    throw new AuthError();
  }
  const body = (await resp.json()) as Envelope<T>;
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`);
  return body.data;
}

export const get = <T = unknown>(path: string) => api<T>(path);
export const post = <T = unknown>(path: string, data?: unknown) =>
  api<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

/** SSE 地址：EventSource 无法携带 header，token 走查询参数 */
export function sseUrl(path: string): string {
  const token = getToken();
  if (!token) return path;
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

/** 导出生成的测试文件（触发浏览器下载） */
export async function downloadGeneratedTest(genCode: string): Promise<string> {
  const data = await get<{ filename: string; content: string }>(`/api/generations/${genCode}/export`);
  const blob = new Blob([data.content], { type: "text/x-python;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = data.filename;
  a.click();
  URL.revokeObjectURL(url);
  return data.filename;
}
