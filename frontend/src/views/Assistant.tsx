import { PageHeader } from "../components/PageHeader";
import { Markdown } from "../components/Markdown";
import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Empty, Input, Popconfirm, Select, Space, Spin, Tag, Timeline, Tooltip, Typography } from "antd";
import {
  DeleteOutlined,
  LoadingOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
  ToolOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { del, get, getToken, post } from "../api";

interface Thread {
  id: number;
  title: string;
  repo_id: number;
  updated_at: string;
}
interface Repo {
  id: number;
  url: string;
}
interface ToolEvent {
  name: string;
  args: Record<string, unknown>;
  summary: string;
  ms?: number;
  running?: boolean;
}
interface Citation {
  ref: string;
  locator: string;
}
interface ChatMsg {
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
};

function argsDigest(args: Record<string, unknown>): string {
  const parts = Object.entries(args).map(([k, v]) => `${k}=${String(v).slice(0, 60)}`);
  return parts.join(" · ") || "-";
}

function UserMsg({ m }: { m: ChatMsg }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", marginBlock: 10 }}>
      <div className="tf-chat-user">
        <UserOutlined style={{ marginInlineEnd: 6, opacity: 0.7 }} />
        {m.content}
      </div>
    </div>
  );
}

function AssistantMsg({ m }: { m: ChatMsg }) {
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
                <Tag color="geekblue" style={{ marginInlineEnd: 6 }}>
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
          {m.content ? (
            <Markdown>{m.content}</Markdown>
          ) : (
            <Spin indicator={<LoadingOutlined />} spinning>
              <span style={{ color: "var(--tf-ink-2)", fontSize: 13 }}>思考中…</span>
            </Spin>
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

function Panel({ children }: { children: ReactNode }) {
  return (
    <div className="ant-card ant-card-bordered" style={{ padding: 10, borderRadius: 12, height: "100%" }}>
      {children}
    </div>
  );
}

export function Assistant() {
  const { message } = App.useApp();
  const qc = useQueryClient();
  const threads = useQuery({ queryKey: ["assistant-threads"], queryFn: () => get<{ threads: Thread[] }>("/api/assistant/threads") });
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<Repo[]>("/api/repos") });
  const [activeId, setActiveId] = useState<number | null>(null);
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [newRepoId, setNewRepoId] = useState<number | undefined>();
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
    mutationFn: () => post<{ id: number }>("/api/assistant/threads", { repo_id: newRepoId ?? 0 }),
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

  const send = async () => {
    const content = input.trim();
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
                if (last >= 0) events[last] = { ...events[last], summary: (data.summary as string) ?? "", running: false, ms: (data.ms as number) ?? 0 };
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

  return (
    <div>
      <PageHeader title="AI 助手" subtitle="对仓库提问：混合检索 + 知识图谱 + 源码阅读的工具循环调查，回答全部带引用溯源" />
      <div style={{ display: "flex", gap: 16, alignItems: "stretch" }}>
        <div style={{ width: 240, flexShrink: 0 }}>
          <Panel>
            <Space.Compact style={{ width: "100%", marginBottom: 8 }}>
              <Select
                size="small"
                style={{ minWidth: 0, flex: 1 }}
                placeholder="限定仓库（可选）"
                allowClear
                value={newRepoId}
                onChange={setNewRepoId}
                options={(repos.data ?? []).map((r) => ({
                  value: r.id,
                  label: `#${r.id} ${String(r.url).split("/").pop()?.replace(/\.git$/, "")}`,
                }))}
              />
              <Button size="small" type="primary" icon={<PlusOutlined />} loading={createThread.isPending} onClick={() => createThread.mutate()}>
                新对话
              </Button>
            </Space.Compact>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {(threads.data?.threads ?? []).map((t) => (
                <div
                  key={t.id}
                  onClick={() => setActiveId(t.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "6px 8px",
                    borderRadius: 8,
                    cursor: "pointer",
                    background: t.id === activeId ? "rgba(79,70,229,0.1)" : "transparent",
                    fontWeight: t.id === activeId ? 600 : 400,
                  }}
                >
                  <Typography.Text ellipsis style={{ flex: 1, fontSize: 13 }} title={t.title}>
                    {t.title}
                  </Typography.Text>
                  <Popconfirm
                    title="删除该会话？"
                    onConfirm={(e) => {
                      e?.stopPropagation();
                      removeThread.mutate(t.id);
                    }}
                    onCancel={(e) => e?.stopPropagation()}
                  >
                    <Button
                      size="small"
                      type="text"
                      icon={<DeleteOutlined />}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Popconfirm>
                </div>
              ))}
              {threads.data?.threads.length === 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  暂无会话，点上方「新对话」开始
                </Typography.Text>
              )}
            </div>
          </Panel>
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            ref={scrollRef}
            style={{ height: "calc(100vh - 268px)", overflowY: "auto", padding: "4px 6px" }}
          >
            {msgs.length === 0 ? (
              <Empty
                description={
                  <span style={{ color: "var(--tf-ink-2)" }}>
                    试试问：『hybrid_search 怎么实现的？』『需求到用例的链路是什么？』『改 auth.py 会影响哪些函数？』
                  </span>
                }
                style={{ marginBlockStart: 80 }}
              />
            ) : (
              msgs.map((m) => (
                <Fragment key={m.id}>{m.role === "user" ? <UserMsg m={m} /> : <AssistantMsg m={m} />}</Fragment>
              ))
            )}
          </div>
          <div style={{ marginBlockStart: 10, display: "flex", gap: 8 }}>
            <Input.TextArea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              placeholder={activeId === null ? "先新建一个会话" : "提问…（Enter 发送，Shift+Enter 换行）"}
              autoSize={{ minRows: 1, maxRows: 5 }}
              disabled={activeId === null || streaming}
              style={{ borderRadius: 10 }}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={streaming}
              disabled={activeId === null || !input.trim()}
              onClick={() => void send()}
              style={{ borderRadius: 10 }}
            >
              发送
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
