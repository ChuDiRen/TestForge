/** 后端统一封套 {code, message, data} */
export interface Envelope<T = unknown> {
  code: number;
  message: string;
  data: T;
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = (await resp.json()) as Envelope<T>;
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`);
  return body.data;
}

export const get = <T = unknown>(path: string) => api<T>(path);
export const post = <T = unknown>(path: string, data?: unknown) =>
  api<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });
