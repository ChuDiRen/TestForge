/** 文件导出/下载类接口：生成用例导出、KG 导出等浏览器下载 */

import { get } from './request'

/** 导出生成的测试文件（触发浏览器下载） */
export async function downloadGeneratedTest(genCode: string): Promise<string> {
  const data = await get<{ filename: string; content: string }>(`/api/generations/${genCode}/export`)
  const blob = new Blob([data.content], { type: 'text/x-python;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = data.filename
  a.click()
  URL.revokeObjectURL(url)
  return data.filename
}

/** base64 → 浏览器下载（KG 导出：json/csv/xlsx/graphml/zip） */
export function downloadB64(filename: string, contentB64: string): void {
  const bin = atob(contentB64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  const url = URL.createObjectURL(new Blob([bytes]))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
