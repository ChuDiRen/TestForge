import { Fragment, useMemo, useState, type ReactNode } from 'react'
import { Button, message } from 'antd'

const TOKEN_RE = /("(?:\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g

const COLOR = {
  key: '#a626a4',
  string: '#22863a',
  number: '#005cc5',
  literal: '#d73a49',
  plain: '#24292e',
}

/** JSON 语法高亮：纯正则分词产出 React 节点，不使用 innerHTML */
function highlight(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  TOKEN_RE.lastIndex = 0
  while ((m = TOKEN_RE.exec(text)) !== null) {
    if (m.index > last)
      nodes.push(
        <span key={i++} style={{ color: COLOR.plain }}>
          {text.slice(last, m.index)}
        </span>,
      )
    if (m[1] !== undefined) {
      // 字符串：后跟冒号的是键
      const isKey = m[2] !== undefined
      nodes.push(
        <span key={i++} style={{ color: isKey ? COLOR.key : COLOR.string, fontWeight: isKey ? 600 : 400 }}>
          {m[1]}
        </span>,
      )
      if (isKey)
        nodes.push(
          <span key={i++} style={{ color: COLOR.plain }}>
            {m[2]}
          </span>,
        )
    } else if (m[3] !== undefined) {
      nodes.push(
        <span key={i++} style={{ color: COLOR.literal, fontWeight: 600 }}>
          {m[3]}
        </span>,
      )
    } else if (m[4] !== undefined) {
      nodes.push(
        <span key={i++} style={{ color: COLOR.number }}>
          {m[4]}
        </span>,
      )
    }
    last = m.index + m[0].length
  }
  if (last < text.length)
    nodes.push(
      <span key={i++} style={{ color: COLOR.plain }}>
        {text.slice(last)}
      </span>,
    )
  return nodes
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  // http 局域网访问（手机）无 clipboard API：textarea 兜底
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  document.execCommand('copy')
  document.body.removeChild(ta)
}

interface JsonProps {
  /** 对象 / 数组 / 已序列化的 JSON 字符串 / 任意可展示值 */
  data: unknown
  /** 最大高度（px），超出滚动；默认 420 */
  maxHeight?: number
}

/** JSON 展示组件：语法高亮 + 一键复制 + 窄屏不溢出 */
export function Json({ data, maxHeight = 420 }: JsonProps) {
  const text = useMemo(() => {
    if (typeof data === 'string') {
      try {
        return JSON.stringify(JSON.parse(data), null, 2)
      } catch {
        return data
      }
    }
    try {
      return JSON.stringify(data, null, 2) ?? String(data)
    } catch {
      return String(data)
    }
  }, [data])
  const nodes = useMemo(() => highlight(text), [text])
  const [copied, setCopied] = useState(false)

  const onCopy = async () => {
    try {
      await copyText(text)
      setCopied(true)
      message.success('已复制')
      setTimeout(() => setCopied(false), 1500)
    } catch {
      message.error('复制失败')
    }
  }

  return (
    <div style={{ position: 'relative', maxWidth: '100%' }}>
      <pre
        className="json-body"
        style={{
          background: '#f6f8fa',
          border: '1px solid #ebedf0',
          padding: 12,
          borderRadius: 6,
          fontSize: 12,
          lineHeight: 1.6,
          overflow: 'auto',
          maxHeight,
          margin: 0,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {nodes.length ? nodes : <Fragment>{text}</Fragment>}
      </pre>
      <Button
        size="small"
        type="text"
        onClick={onCopy}
        style={{ position: 'absolute', top: 4, right: 6, fontSize: 12, color: '#8c8c8c' }}
        aria-label="复制 JSON"
      >
        {copied ? '✓' : '复制'}
      </Button>
    </div>
  )
}
