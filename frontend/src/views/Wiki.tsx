import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Badge, Button, Card, Drawer, Popconfirm, Space, Table, Tabs, Tag, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { Markdown } from "../components/Markdown";
import { useIsMobile } from "../hooks";

interface WikiPageRow {
  id: number;
  repo_id: number;
  level: string;
  title: string;
  module: string;
  function: string;
  rev: number;
  stale: boolean;
}

export function Wiki() {
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const [repoId, setRepoId] = useState(0);
  const [detail, setDetail] = useState<WikiPageRow | null>(null);
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  const pages = useQuery({
    queryKey: ["wiki", repoId],
    queryFn: () => get<WikiPageRow[]>(`/api/wiki${repoId ? `?repo_id=${repoId}` : ""}`),
    refetchInterval: 10000,
  });
  const detailQ = useQuery({
    queryKey: ["wiki-page", detail?.id],
    queryFn: () => get<any>(`/api/wiki/${detail!.id}`),
    enabled: !!detail,
  });
  const rebuild = useMutation({
    mutationFn: (full: boolean) => post("/api/wiki/rebuild", { repo_id: repoId, full }),
    onSuccess: () => {
      message.success("重建完成");
      qc.invalidateQueries({ queryKey: ["wiki"] });
    },
  });

  const data = pages.data ?? [];
  const staleCount = data.filter((p) => p.stale).length;
  const levelTab = (level: string) => (
    <Table<WikiPageRow>
      rowKey="id"
      size="small"
      scroll={{ x: 480 }}
      pagination={{ pageSize: 10 }}
      dataSource={data.filter((p) => p.level === level)}
      onRow={(r) => ({ onClick: () => setDetail(r), style: { cursor: "pointer" } })}
      columns={[
        { title: "标题", dataIndex: "title" },
        ...(level !== "repo" ? [{ title: "模块", dataIndex: "module" }] : []),
        { title: "rev", dataIndex: "rev", width: 60 },
        {
          title: "状态",
          dataIndex: "stale",
          width: 90,
          render: (s: boolean) => (s ? <Badge status="warning" text="stale" /> : <Badge status="success" text="最新" />),
        },
      ]}
    />
  );

  return (
    <div>
      <PageHeader title="代码库 / Wiki" subtitle="预编译知识层：函数卡片 / 模块页 · 增量重建 · stale 传播" />
      <Card
        title="代码库 / Wiki（预编译知识层）"
        extra={
          <Space wrap>
            <select value={repoId} onChange={(e) => setRepoId(Number(e.target.value))} style={{ padding: 4 }}>
              <option value={0}>全部仓库</option>
              {(repos.data ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  #{r.id} {String(r.url).split("/").pop()?.replace(/\.git$/, "") || r.url}
                </option>
              ))}
            </select>
            <Badge count={staleCount} offset={[-4, 0]}>
              <Button size="small" onClick={() => rebuild.mutate(false)} disabled={!repoId}>
                重建 stale 页
              </Button>
            </Badge>
            <Popconfirm title="全量重编译？" onConfirm={() => rebuild.mutate(true)}>
              <Button size="small" type="primary" disabled={!repoId}>
                一键重建全部
              </Button>
            </Popconfirm>
          </Space>
        }
      >
        {staleCount > 0 && (
          <div style={{ background: "#fff7e6", border: "1px solid #ffd591", padding: "8px 12px", borderRadius: 6, marginBottom: 12 }}>
            ⚠️ {staleCount} 个页面因源码变更已标记 stale，重建后 rev+1 并清除标记。
          </div>
        )}
        <Tabs
          items={[
            { key: "repo", label: "仓库总览", children: levelTab("repo") },
            { key: "module", label: "模块页", children: levelTab("module") },
            { key: "function", label: "函数卡片", children: levelTab("function") },
          ]}
        />
      </Card>
      <Drawer title={detail?.title} open={!!detail} onClose={() => setDetail(null)} width={isMobile ? "100%" : 640}>
        {detailQ.data && (
          <>
            <Space style={{ marginBottom: 12 }} wrap>
              <Tag>rev {detailQ.data.rev}</Tag>
              <Tag color={detailQ.data.stale ? "orange" : "green"}>{detailQ.data.stale ? "stale" : "最新"}</Tag>
            </Space>
            <Markdown>{detailQ.data.content_md}</Markdown>
          </>
        )}
      </Drawer>
    </div>
  );
}
