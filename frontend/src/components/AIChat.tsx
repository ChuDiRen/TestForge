/**
 * AI 对话共享层：useChat 状态机 + 消息流 + 输入框 + 全局悬浮球入口。
 *
 * 页面版（views/Assistant.tsx）与悬浮球 Drawer 复用同一套 hook 与渲染件，
 * 两处实例状态独立。悬浮球对标 GitNexus 的 QueryFAB：任何页面可随时唤起。
 */
import { Markdown } from "./Markdown";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Drawer, Empty, Input, Select, Space, Spin, Tag, Timeline, Tooltip, Typography } from "antd";
import {
  CloseOutlined,
  LoadingOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
  ToolOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { del, get, getToken, post } from "../api";

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
};

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

export function AssistantMsg({ m }: { m: ChatMsg }) {
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

  return { threads, repos, activeId, setActiveId, msgs, input, setInput, streaming, scrollRef, createThread, removeThread, send };
}

export function MessagesView({ chat, height }: { chat: ReturnType<typeof useChat>; height: string }) {
  const { msgs, scrollRef } = chat;
  return (
    <div ref={scrollRef} style={{ height, overflowY: "auto", padding: "4px 6px" }}>
      {msgs.length === 0 ? (
        <Empty
          description={
            <span style={{ color: "var(--tf-ink-2)" }}>
              试试问：『hybrid_search 怎么实现的？』『需求到用例的链路是什么？』『改 auth.py 会影响哪些函数？』
            </span>
          }
          style={{ marginBlockStart: 60 }}
        />
      ) : (
        msgs.map((m) => (m.role === "user" ? <UserMsg key={m.id} m={m} /> : <AssistantMsg key={m.id} m={m} />))
      )}
    </div>
  );
}

export function ChatInput({ chat }: { chat: ReturnType<typeof useChat> }) {
  const { input, setInput, streaming, send, activeId } = chat;
  return (
    <div style={{ display: "flex", gap: 8 }}>
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
  );
}

/** 全局悬浮球 + 对话抽屉（任何页面可唤起，对标 GitNexus QueryFAB） */
export function ChatFab() {
  const [open, setOpen] = useState(false);
  const chat = useChat();
  const repoLabel = (url: string, id: number) => `#${id} ${String(url).split("/").pop()?.replace(/\.git$/, "")}`;

  return (
    <>
      {!open && (
        <button className="tf-chat-fab" aria-label="打开 AI 助手" onClick={() => setOpen(true)}>
          <RobotOutlined style={{ fontSize: 22 }} />
          <span className="tf-chat-fab-tip">AI 助手</span>
        </button>
      )}
      <Drawer
        title={
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <RobotOutlined style={{ color: "var(--tf-primary)" }} /> AI 助手
          </span>
        }
        placement="right"
        width={520}
        open={open}
        onClose={() => setOpen(false)}
        closeIcon={<CloseOutlined />}
        styles={{ body: { padding: "8px 12px", display: "flex", flexDirection: "column" }, header: { paddingBlock: 10 } }}
      >
        <Space.Compact style={{ marginBottom: 8 }}>
          <Select
            size="small"
            style={{ minWidth: 0, flex: 1 }}
            placeholder="选择会话"
            value={chat.activeId ?? undefined}
            onChange={(v) => chat.setActiveId(v)}
            options={chat.threads.data?.threads.map((t) => ({ value: t.id, label: t.title }))}
          />
          <Button
            size="small"
            icon={<PlusOutlined />}
            loading={chat.createThread.isPending}
            onClick={() => chat.createThread.mutate(0)}
          >
            新对话
          </Button>
        </Space.Compact>
        <MessagesView chat={chat} height="calc(100vh - 210px)" />
        <ChatInput chat={chat} />
      </Drawer>
    </>
  );
}
