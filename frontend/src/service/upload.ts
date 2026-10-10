/** multipart 文件上传类接口：知识管线文档 / 代码包建仓 / 知识资产 */

import { type Envelope, getToken } from './request'

/** multipart 文件上传（知识管线：pdf/docx/txt/md/csv → 异步分块/抽取/建图） */
export async function uploadKgDoc(
  file: File,
  opts: { workspace?: string; repo_id?: number; title?: string; parser?: string } = {},
): Promise<{ doc_key: string; status: string; title: string; parse_meta: Record<string, unknown> }> {
  const token = getToken()
  const form = new FormData()
  form.append('file', file)
  if (opts.workspace) form.append('workspace', opts.workspace)
  if (opts.repo_id) form.append('repo_id', String(opts.repo_id))
  if (opts.title) form.append('title', opts.title)
  if (opts.parser) form.append('parser', opts.parser)
  const resp = await fetch('/api/kg/documents/upload', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  })
  const body = (await resp.json()) as Envelope<{ doc_key: string; status: string; title: string; parse_meta: Record<string, unknown> }>
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`)
  return body.data
}

/** 上传代码包建仓（入口①）：zip → 安全解压 → tree-sitter 索引 → 调用图谱 → Wiki 编译 */
export async function uploadRepoZip(
  file: File,
  name: string,
): Promise<{ id: number; name: string; url: string; status: string; functions: number; call_edges: number; wiki_pages: number; steps: string[] }> {
  const token = getToken()
  const form = new FormData()
  form.append('file', file)
  form.append('name', name)
  const resp = await fetch('/api/repos/upload', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  })
  const body = (await resp.json()) as Envelope<{
    id: number
    name: string
    url: string
    status: string
    functions: number
    call_edges: number
    wiki_pages: number
    steps: string[]
  }>
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`)
  return body.data
}

/** 知识资产统一上传（入口②）：需求文档/技术文档/测试方案/历史缺陷 */
export async function uploadKnowledgeAsset(file: File, opts: { kind: string; repo_id?: number; title?: string }): Promise<Record<string, unknown>> {
  const token = getToken()
  const form = new FormData()
  form.append('file', file)
  form.append('kind', opts.kind)
  if (opts.repo_id) form.append('repo_id', String(opts.repo_id))
  if (opts.title) form.append('title', opts.title)
  const resp = await fetch('/api/knowledge/assets/upload', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  })
  const body = (await resp.json()) as Envelope<Record<string, unknown>>
  if (body.code !== 0) throw new Error(body.message || `code=${body.code}`)
  return body.data
}
