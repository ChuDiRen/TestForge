/**
 * AI 对话共享层：useChat 状态机 + 消息流 + 输入框。
 *
 * AI 助手页面（views/Assistant.tsx）复用本文件的状态机与渲染件。
 */
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Empty, Input, Spin, Steps, Tag, Timeline, Tooltip, Typography } from "antd";
import { LoadingOutlined, RobotOutlined, SendOutlined, ThunderboltOutlined, ToolOutlined, UserOutlined } from "@ant-design/icons";
import { del, get, getToken, post, sseUrl } from "../api";

export interface Thread {
  id: number;
  title: string;
  repo_id: number;
  updated_at: string;
}
interface Repo {
  id: number;
  url: string;
}
export interface ToolEvent {
  name: string;
  args: Record<string, unknown>;
  summary: string;
  ms?: number;
  running?: boolean;
  /** 工具结构化结果（后端随 tool_end 下发）——generate_case 的进度卡靠它拿 generation_code */
  result?: Record<string, unknown> | null;
}
export interface Citation {
  ref: string;
  locator: string;
}
export interface ChatMsg {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  tool_events: ToolEvent[];
  citations: Citation[];
  pending?: boolean;
}

const TOOL_LABEL: Record<string, string> = {
  search: "混合检索",
  explore: "图谱导航",
  read: "读取源码",
  impact: "影响面分析",
  overview: "仓库概览",
  cases: "查询用例",
  generate_case: "生成用例",
  generation_status: "生成进度查询",
  create_requirement: "录入需求",
  grep: "源码搜索",
  glob: "文件查找",
  ls: "列目录",
  read_file: "读文件",
  write_todos: "任务规划",
  task: "子代理",
};

const CITE_TOKEN_RE = /\[\[[^\]]+\]\]/g;

/** 把回答里的 [[引用]] 包成 inline code，交给 Markdown 自定义 code 渲染成可点击标签 */
function prepareCitations(content: string): string {
  return content.replace(CITE_TOKEN_RE, (m) => `\`${m}\``);
}

/** 引用标签点击跳转：Function/Module/Class → 知识图谱；文件路径 → 代码库/Wiki */
function citeTarget(token: string): string {
  const inner = token.slice(2, -2);
  if (inner.startsWith("Function:") || inner.startsWith("Module:") || inner.startsWith("Class:")) return "graph";
  return "wiki";
}

function CitationCode({ children }: { children?: ReactNode }) {
  const raw = String(children ?? "").replace(/\s+/g, "");
  if (!raw.startsWith("[[")) return <code>{raw}</code>;
  return (
    <Tag
      color="purple"
      style={{ fontSize: 11.5, cursor: "pointer", marginInline: 1 }}
      onClick={() => window.dispatchEvent(new CustomEvent("tf-navigate", { detail: citeTarget(raw) }))}
      title="点击跳转到对应页面"
    >
      {raw}
    </Tag>
  );
}

/** Mermaid 流程图渲染（GitNexus 聊天 MermaidDiagram 对齐）：```mermaid 代码块动态渲染，
 *  失败降级为原文本。动态 import 不进首屏 bundle。 */
function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mer = (await import("mermaid")).default;
        mer.initialize({
          startOnLoad: false,
          theme: document.documentElement.dataset.theme === "dark" ? "dark" : "neutral",
          securityLevel: "strict",
        });
        const { svg } = await mer.render(`mmd-${Math.random().toString(36).slice(2)}`, code);
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        if (!cancelled) setErr(String(e).slice(0, 150));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code]);
  if (err)
    return (
      <pre style={{ fontSize: 12, color: "var(--tf-ink-3)", background: "var(--tf-bg)", borderRadius: 8, padding: 10, overflowX: "auto" }}>
        {`流程图渲染失败：${err}\n${code}`}
      </pre>
    );
  return (
    <div
      ref={ref}
      style={{ background: "var(--tf-panel, #fff)", border: "1px solid var(--tf-line)", borderRadius: 8, padding: 10, overflowX: "auto", marginBlock: 8 }}
    />
  );
}

/** AI 专用 Markdown：引用可点击跳转、mermaid 代码块渲染流程图，其余排版同全站 */
function AiMarkdown({ content }: { content: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          code: (props: any) => {
            const cls = String(props.className || "");
            if (cls.includes("language-mermaid")) {
              return <MermaidBlock code={String(props.children ?? "").replace(/\n$/, "")} />;
            }
            return <CitationCode>{props.children}</CitationCode>;
          },
        }}
      >
        {prepareCitations(content)}
      </ReactMarkdown>
    </div>
  );
}

function argsDigest(args: Record<string, unknown>): string {
  const parts = Object.entries(args).map(([k, v]) => `${k}=${String(v).slice(0, 60)}`);
  return parts.join(" · ") || "-";
}

export function UserMsg({ m }: { m: ChatMsg }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", marginBlock: 10 }}>
      <div className="tf-chat-user">
        <UserOutlined style={{ marginInlineEnd: 6, opacity: 0.7 }} />
        {m.content}
      </div>
    </div>
  );
}

const STAGES = ["PLAN 清单", "覆盖守卫", "代码生成", "沙箱执行", "覆盖率回填"];

/** 生成进度卡：generate_case 入队后内嵌在对话消息里，SSE 实时跟踪
 *  PLAN→守卫→生成→沙箱→回填 五阶段，完成后展示结果并可跳用例库。
 *  这就是「工作台流程融入智能体」的载体——用户不用离开对话。 */
function GenerationProgressCard({ genId }: { genId: string }) {
  const [stage, setStage] = useState(-1);
  const [logs, setLogs] = useState<string[]>([]);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [failed, setFailed] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(sseUrl(`/api/generations/${genId}/events`));
    esRef.current = es;
    es.addEventListener("stage", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setLogs((l) => [...l, `[${d.stage}] ${d.message}`]);
      const idx = STAGES.indexOf(d.stage);
      if (idx >= 0) setStage(idx + 1);
    });
    es.addEventListener("result", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      try {
        setResult(JSON.parse(d.payload_json || "{}"));
      } catch {
        setResult({});
      }
      if (d.stage === "failed" || String(d.message || "").includes("失败")) setFailed(true);
      setStage(STAGES.length);
      es.close();
    });
    es.onerror = () => {
      es.close();
    };
    return () => es.close();
  }, [genId]);

  const done = stage >= STAGES.length;
  return (
    <div
      style={{
        border: "1px solid var(--tf-line-strong)",
        borderRadius: 10,
        padding: "10px 12px",
        marginBlockEnd: 10,
        background: "var(--tf-panel, #fff)",
        maxWidth: 560,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBlockEnd: 8 }}>
        <ThunderboltOutlined style={{ color: "var(--tf-primary)" }} />
        <span style={{ fontSize: 13, fontWeight: 600 }}>用例生成 {genId}</span>
        <span style={{ flex: 1 }} />
        {done ? (
          <Tag color={failed ? "red" : "green"}>{failed ? "生成失败" : "完成"}</Tag>
        ) : (
          <Tag color="processing">生成中…</Tag>
        )}
      </div>
      <Steps
        size="small"
        current={stage}
        status={failed ? "error" : done ? "finish" : "process"}
        items={STAGES.map((s) => ({ title: s }))}
      />
      {logs.length > 0 && (
        <div style={{ marginBlockStart: 8, maxHeight: 88, overflowY: "auto", fontSize: 12, color: "var(--tf-ink-2)", fontFamily: "Consolas, monospace" }}>
          {logs.slice(-6).map((l, i) => (
            <div key={i} style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {l}
            </div>
          ))}
        </div>
      )}
      {done && !failed && (
        <div style={{ marginBlockStart: 8, display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 12.5, color: "var(--tf-ink-2)" }}>
            {result?.total !== undefined ? `生成 ${result.total} 条 · 沙箱通过 ${result.passed ?? "?"} 条` : "用例已入库"}
          </span>
          <span style={{ flex: 1 }} />
          <Button
            size="small"
            type="primary"
            ghost
            onClick={() => window.dispatchEvent(new CustomEvent("tf-navigate", { detail: "cases" }))}
          >
            去用例库查看
          </Button>
        </div>
      )}
    </div>
  );
}

export function AssistantMsg({ m }: { m: ChatMsg }) {
  const genEvent = m.tool_events.find((t) => t.name === "generate_case" && (t.result as any)?.generation_code);
  const genId = genEvent ? String((genEvent.result as any).generation_code) : null;
  return (
    <div style={{ marginBlock: 14 }}>
      {m.tool_events.length > 0 && (
        <Timeline
          style={{ marginBlockEnd: 10 }}
          items={m.tool_events.map((t, i) => ({
            key: i,
            color: t.running ? "blue" : "gray",
            dot: t.running ? <Spin indicator={<LoadingOutlined />} size="small" /> : <ToolOutlined />,
            children: (
              <div style={{ fontSize: 12.5 }}>
                <Tag color="cyan" style={{ marginInlineEnd: 6 }}>
                  {TOOL_LABEL[t.name] ?? t.name}
                </Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {argsDigest(t.args)}
                  {t.ms !== undefined && ` · ${t.ms}ms`}
                </Typography.Text>
              </div>
            ),
          }))}
        />
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <RobotOutlined style={{ color: "var(--tf-primary)", fontSize: 16, marginBlockStart: 3 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          {genId && <GenerationProgressCard genId={genId} />}
          {m.content ? (
            <AiMarkdown content={m.content} />
          ) : (
            !genId && (
              <Spin indicator={<LoadingOutlined />} spinning>
                <span style={{ color: "var(--tf-ink-2)", fontSize: 13 }}>思考中…</span>
              </Spin>
            )
          )}
          {m.citations.length > 0 && (
            <div style={{ marginBlockStart: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
              {m.citations.map((c, i) => (
                <Tooltip key={i} title={`${c.ref} · ${c.locator}`}>
                  <Tag color="purple" style={{ fontSize: 11 }}>
                    [[{c.ref}:{c.locator}]]
                  </Tag>
                </Tooltip>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** 对话状态机：页面与悬浮球共用（两处实例状态独立） */
export function useChat() {
  const { message } = App.useApp();
  const qc = useQueryClient();
  const threads = useQuery({ queryKey: ["assistant-threads"], queryFn: () => get<{ threads: Thread[] }>("/api/assistant/threads") });
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<Repo[]>("/api/repos") });
  const [activeId, setActiveId] = useState<number | null>(null);
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const history = useQuery({
    queryKey: ["assistant-msgs", activeId],
    queryFn: () => get<{ messages: ChatMsg[] }>(`/api/assistant/threads/${activeId}/messages`),
    enabled: activeId !== null,
  });

  useEffect(() => {
    if (activeId === null && threads.data?.threads.length) setActiveId(threads.data.threads[0].id);
  }, [threads.data, activeId]);
  useEffect(() => {
    if (history.data) setMsgs(history.data.messages);
  }, [history.data]);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [msgs]);

  const createThread = useMutation({
    mutationFn: (repo_id: number = 0) => post<{ id: number }>("/api/assistant/threads", { repo_id }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ["assistant-threads"] });
      setActiveId(t.id);
      setMsgs([]);
    },
  });
  const removeThread = useMutation({
    mutationFn: (id: number) => del(`/api/assistant/threads/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assistant-threads"] });
      setActiveId(null);
      setMsgs([]);
    },
  });

  const send = async (override?: string) => {
    // override：Enter 事件直接带 DOM 里的当前值，绕开受控 state 批处理竞态
    const content = (override ?? input).trim();
    if (!content || streaming || activeId === null) return;
    setInput("");
    setStreaming(true);
    setMsgs((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", content, tool_events: [], citations: [] },
      { id: `a-${Date.now()}`, role: "assistant", content: "", tool_events: [], citations: [], pending: true },
    ]);
    const patchPending = (patch: Partial<ChatMsg>) =>
      setMsgs((prev) => prev.map((m) => (m.pending ? { ...m, ...patch } : m)));
    try {
      const resp = await fetch(`/api/assistant/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ thread_id: activeId, content }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        for (;;) {
          const idx = buf.indexOf("\n\n");
          if (idx < 0) break;
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const lines = frame.split("\n");
          const etype = lines.find((l) => l.startsWith("event: "))?.slice(7);
          const dataRaw = lines.find((l) => l.startsWith("data: "))?.slice(6);
          if (!etype || !dataRaw) continue;
          const data = JSON.parse(dataRaw) as Record<string, unknown>;
          if (etype === "token") {
            const text = data.text as string;
            setMsgs((prev) => prev.map((m) => (m.pending ? { ...m, content: m.content + text } : m)));
          } else if (etype === "tool_start") {
            const ev: ToolEvent = { name: data.name as string, args: (data.args ?? {}) as Record<string, unknown>, summary: "", running: true };
            setMsgs((prev) => prev.map((m) => (m.pending ? { ...m, tool_events: [...m.tool_events, ev] } : m)));
          } else if (etype === "tool_end") {
            setMsgs((prev) =>
              prev.map((m) => {
                if (!m.pending) return m;
                const events = [...m.tool_events];
                const last = events.findIndex((t) => t.running);
                if (last >= 0)
                  events[last] = {
                    ...events[last],
                    summary: (data.summary as string) ?? "",
                    running: false,
                    ms: (data.ms as number) ?? 0,
                    result: (data.result as Record<string, unknown> | null) ?? null,
                  };
                return { ...m, tool_events: events };
              }),
            );
          } else if (etype === "done") {
            patchPending({ pending: false, citations: (data.citations as Citation[]) ?? [] });
          } else if (etype === "error") {
            message.error((data.message as string) ?? "助手执行失败");
            patchPending({ pending: false, content: (data.message as string) ?? "执行失败" });
          }
        }
      }
      setMsgs((prev) => prev.map((m) => (m.pending ? { ...m, pending: false } : m)));
      qc.invalidateQueries({ queryKey: ["assistant-threads"] });
    } catch (e) {
      message.error(`对话失败: ${e instanceof Error ? e.message : String(e)}`);
      patchPending({ pending: false });
    } finally {
      setStreaming(false);
    }
  };

  return { threads, repos, activeId, setActiveId, msgs, input, setInput, streaming, scrollRef, createThread, removeThread, send };
}

const SUGGESTIONS = [
  "给 sanitize_text 函数生成单元测试用例",
  "给 sanitize_text 生成功能测试用例（按业务场景）",
  "为平台生成接口测试用例（抓取网关 OpenAPI）",
  "生成一条 E2E 测试用例跑通平台真实旅程",
  "我把需求文档贴给你，你存入知识库后给相关函数生成用例",
];

export function MessagesView({ chat, height }: { chat: ReturnType<typeof useChat>; height: string }) {
  const { msgs, scrollRef, setInput } = chat;
  return (
    <div ref={scrollRef} style={{ height, overflowY: "auto", padding: "4px 6px" }}>
      {msgs.length === 0 ? (
        <div style={{ marginBlockStart: 24 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
            四层用例生成都可以在这里完成——点一条直接开始，或直接描述你的要求：
          </Typography.Text>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBlockStart: 10 }}>
            {SUGGESTIONS.map((s) => (
              <Tag.CheckableTag
                key={s}
                checked={false}
                onChange={() => setInput(s)}
                style={{
                  border: "1px solid var(--tf-line, #e0e2f0)",
                  borderRadius: 8,
                  padding: "5px 10px",
                  fontSize: 12.5,
                  whiteSpace: "normal",
                  width: "100%",
                  cursor: "pointer",
                }}
              >
                {s}
              </Tag.CheckableTag>
            ))}
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 11.5, display: "block", marginBlockStart: 10 }}>
            提示：生成高质量用例可以把 PRD / 接口文档 / 业务规则贴进对话，AI 会存入知识库并在生成时自动引用。
          </Typography.Text>
        </div>
      ) : (
        msgs.map((m) => (m.role === "user" ? <UserMsg key={m.id} m={m} /> : <AssistantMsg key={m.id} m={m} />))
      )}
    </div>
  );
}

export function ChatInput({ chat }: { chat: ReturnType<typeof useChat> }) {
  const { input, setInput, streaming, send, activeId, threads } = chat;
  const notReady = activeId === null;
  return (
    <div style={{ display: "flex", gap: 8 }}>
      <Input.TextArea
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onPressEnter={(e) => {
          if (!e.shiftKey) {
            e.preventDefault();
            const text = (e.target as HTMLTextAreaElement).value;
            void send(text);
          }
        }}
        placeholder={threads.isLoading ? "正在加载会话…" : notReady ? "先新建一个会话" : "提问…（Enter 发送，Shift+Enter 换行）"}
        autoSize={{ minRows: 1, maxRows: 5 }}
        disabled={notReady || streaming}
        style={{ borderRadius: 10 }}
      />
      <Button
        type="primary"
        icon={<SendOutlined />}
        loading={streaming}
        disabled={notReady || !input.trim()}
        onClick={() => void send()}
        style={{ borderRadius: 10 }}
      >
        发送
      </Button>
    </div>
  );
}
