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

// ---------------- LightRAG 管线扩展（multipart 上传 / SSE 流式 / 文件下载） ----------------

/** multipart 文件上传（知识管线：pdf/docx/txt/md/csv → 异步分块/抽取/建图） */
export async function uploadKgDoc(
  file: File,
  opts: { workspace?: string; repo_id?: number; title?: string; parser?: string } = {},
): Promise<{ doc_key: string; status: string; title: string; parse_meta: Record<string, unknown> }> {
  const token = getToken();
  const form = new FormData();
  form.append("file", file);
  if (opts.workspace) form.append("workspace", opts.workspace);
  if (opts.repo_id) form.append("repo_id", String(opts.repo_id));
  if (opts.title) form.append("title", opts.title);
  if (opts.parser) form.append("parser", opts.parser);
  const resp = await fetch("/api/kg/documents/upload", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });
  const body = (await resp.json()) as Envelope<{ doc_key: string; status: string; title: string; parse_meta: Record<string, unknown> }>;
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`);
  return body.data;
}

/** 上传代码包建仓（入口①）：zip → 安全解压 → tree-sitter 索引 → 调用图谱 → Wiki 编译 */
export async function uploadRepoZip(
  file: File,
  name: string,
): Promise<{ id: number; name: string; url: string; status: string; functions: number; call_edges: number; wiki_pages: number; steps: string[] }> {
  const token = getToken();
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  const resp = await fetch("/api/repos/upload", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });
  const body = (await resp.json()) as Envelope<{ id: number; name: string; url: string; status: string; functions: number; call_edges: number; wiki_pages: number; steps: string[] }>;
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`);
  return body.data;
}

/** 知识资产统一上传（入口②）：需求文档/技术文档/测试方案/历史缺陷 */
export async function uploadKnowledgeAsset(
  file: File,
  opts: { kind: string; repo_id?: number; title?: string },
): Promise<Record<string, unknown>> {
  const token = getToken();
  const form = new FormData();
  form.append("file", file);
  form.append("kind", opts.kind);
  if (opts.repo_id) form.append("repo_id", String(opts.repo_id));
  if (opts.title) form.append("title", opts.title);
  const resp = await fetch("/api/knowledge/assets/upload", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });
  const body = (await resp.json()) as Envelope<Record<string, unknown>>;
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`);
  return body.data;
}

/** POST SSE 流式请求：逐帧回调 data JSON（LightRAG 式 retrieved → delta* → done） */
export async function postStreamSSE<T = Record<string, unknown>>(
  path: string,
  data: unknown,
  onEvent: (ev: T & { type: string }) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(data),
    signal,
  });
  if (resp.status === 401) {
    setToken("");
    throw new AuthError();
  }
  if (!resp.ok || !resp.body) throw new Error(`stream failed: ${resp.status}`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() || "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as T & { type: string });
      } catch {
        /* 非 JSON 帧忽略 */
      }
    }
  }
}

/** base64 → 浏览器下载（KG 导出：json/csv/xlsx/graphml/zip） */
export function downloadB64(filename: string, contentB64: string): void {
  const bin = atob(contentB64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([bytes]));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
