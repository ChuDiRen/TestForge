/** 后端统一封套 {code, message, data} + 认证 token 管理 */

export interface Envelope<T = unknown> {
  code: number;
  message: string;
  data: T;
}

const TOKEN_KEY = "tf_token";
const USER_KEY = "tf_username";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getUsername(): string {
  return localStorage.getItem(USER_KEY) || "";
}

export function setUsername(username: string): void {
  if (username) localStorage.setItem(USER_KEY, username);
  else localStorage.removeItem(USER_KEY);
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
  // 服务端对临近过期的合法 token 自动续签（X-Renewed-Token），前端无感换新
  const renewed = resp.headers.get("X-Renewed-Token");
  if (renewed) setToken(renewed);
  if (resp.status === 401) {
    setToken("");
    // 登录接口的 401 携带后端真实原因（如「用户名或密码错误」），优先透传
    let msg = "未登录或登录已过期";
    try {
      const body = (await resp.json()) as Envelope<unknown>;
      if (body?.message) msg = body.message;
    } catch {
      /* 无响应体时用默认文案 */
    }
    throw new AuthError(msg);
  }
  const body = (await resp.json()) as Envelope<T>;
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`);
  return body.data;
}

export const get = <T = unknown>(path: string) => api<T>(path);
export const post = <T = unknown>(path: string, data?: unknown) =>
  api<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });
export const put = <T = unknown>(path: string, data?: unknown) =>
  api<T>(path, { method: "PUT", body: data === undefined ? undefined : JSON.stringify(data) });
export const del = <T = unknown>(path: string) => api<T>(path, { method: "DELETE" });

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

/** 仓库展示名：取 url 末段并去掉 .git 后缀（TestForge.git → TestForge） */
export const repoName = (url: string) =>
  decodeURIComponent(String(url).replace(/\/+$/, ""))
    .split("/")
    .pop()
    ?.replace(/\.git$/i, "") || String(url);
