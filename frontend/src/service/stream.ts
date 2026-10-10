/** SSE 流式请求：LightRAG 式 retrieved → delta* → done 帧协议 */

import { AuthError, getToken, setToken } from './request'

/** POST SSE 流式请求：逐帧回调 data JSON（LightRAG 式 retrieved → delta* → done） */
export async function postStreamSSE<T = Record<string, unknown>>(
  path: string,
  data: unknown,
  onEvent: (ev: T & { type: string }) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken()
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(data),
    signal,
  })
  if (resp.status === 401) {
    setToken('')
    throw new AuthError()
  }
  if (!resp.ok || !resp.body) throw new Error(`stream failed: ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const frames = buf.split('\n\n')
    buf = frames.pop() || ''
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)) as T & { type: string })
      } catch {
        /* 非 JSON 帧忽略 */
      }
    }
  }
}
