import { PageHeader } from "../components/PageHeader";
import { ChatInput, MessagesView, useChat } from "../components/AIChat";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Popconfirm, Select, Space, Typography } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { del, get, post } from "../api";
import type { Thread } from "../components/AIChat";

/** AI 助手完整页：会话列表 + 对话区（复用 AIChat 共享对话组件） */
export function Assistant() {
  const chat = useChat();
  const qc = useQueryClient();
  const [newRepoId, setNewRepoId] = useState<number | undefined>();

  const repoOptions = (chat.repos.data ?? []).map((r) => ({
    value: r.id,
    label: `#${r.id} ${String(r.url).split("/").pop()?.replace(/\.git$/, "")}`,
  }));

  const createThread = useMutation({
    mutationFn: (repo_id: number) => post<{ id: number }>("/api/assistant/threads", { repo_id }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ["assistant-threads"] });
      chat.setActiveId(t.id);
    },
  });
  const removeThread = useMutation({
    mutationFn: (id: number) => del(`/api/assistant/threads/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assistant-threads"] });
      chat.setActiveId(null);
    },
  });

  return (
    <div>
      <PageHeader title="AI 助手" subtitle="对仓库提问：混合检索 + 知识图谱 + 源码阅读的工具循环调查，回答全部带引用溯源" />
      <div style={{ display: "flex", gap: 16, alignItems: "stretch" }}>
        <div style={{ width: 240, flexShrink: 0 }}>
          <div className="ant-card ant-card-bordered" style={{ padding: 10, borderRadius: 12, height: "100%" }}>
            <Space.Compact style={{ width: "100%", marginBottom: 8 }}>
              <Select
                size="small"
                style={{ minWidth: 0, flex: 1 }}
                placeholder="限定仓库（可选）"
                allowClear
                onChange={(v) => setNewRepoId(v ?? undefined)}
                value={newRepoId}
                options={repoOptions}
              />
              <Button
                size="small"
                type="primary"
                icon={<PlusOutlined />}
                loading={createThread.isPending}
                onClick={() => createThread.mutate(newRepoId ?? 0)}
              >
                新对话
              </Button>
            </Space.Compact>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {(chat.threads.data?.threads ?? []).map((t: Thread) => (
                <div
                  key={t.id}
                  onClick={() => chat.setActiveId(t.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "6px 8px",
                    borderRadius: 8,
                    cursor: "pointer",
                    background: t.id === chat.activeId ? "var(--tf-acc-soft)" : "transparent",
                    fontWeight: t.id === chat.activeId ? 600 : 400,
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
                    <Button size="small" type="text" icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
                  </Popconfirm>
                </div>
              ))}
              {chat.threads.data?.threads.length === 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  暂无会话，点上方「新对话」开始
                </Typography.Text>
              )}
            </div>
          </div>
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <MessagesView chat={chat} height="calc(100vh - 268px)" />
          <div style={{ marginBlockStart: 10 }}>
            <ChatInput chat={chat} />
          </div>
        </div>
      </div>
    </div>
  );
}
