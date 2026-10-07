import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Badge, Button, Card, Drawer, Empty, Input, Popconfirm, Space, Table, Tabs, Tag, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, repoName } from "../api";
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
  // OpenWiki 移植：TF-IDF 互链推荐 + 确定性健康体检
  const relatedQ = useQuery({
    queryKey: ["wiki-related", detail?.id],
    queryFn: () => get<{ id: number; title: string; score: number }[]>(`/api/wiki/${detail!.id}/related`),
    enabled: !!detail,
  });
  const lintQ = useQuery({
    queryKey: ["wiki-lint", repoId],
    queryFn: () => get<{ findings: any[]; checked: { pages: number; modules: number } }>(`/api/wiki/lint?repo_id=${repoId}`),
    enabled: repoId > 0,
  });
  const openPageById = (pid: number) => {
    const row = data.find((x) => x.id === pid);
    if (row) setDetail(row);
  };

  // Wiki 问答（OpenWiki wiki_ask 移植）：检索知识库 → LLM 依据资料作答 → 带来源
  const [askOpen, setAskOpen] = useState(false);
  const [askQ, setAskQ] = useState("");
  const [asking, setAsking] = useState(false);
  const [askMsgs, setAskMsgs] = useState<{ q: string; a: string; sources: { id: number; title: string }[] }[]>([]);
  const ask = async () => {
    const q = askQ.trim();
    if (!q || asking || !repoId) return;
    setAsking(true);
    setAskQ("");
    try {
      const history = askMsgs
        .slice(-3)
        .map((m) => `问：${m.q}`)
        .join("\n");
      const res = await post<{ answer: string; sources: { id: number; title: string }[] }>("/api/wiki/ask", {
        question: q,
        repo_id: repoId,
        history,
      });
      setAskMsgs((m) => [...m, { q, a: res.answer, sources: res.sources ?? [] }]);
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setAsking(false);
    }
  };
  const [docTitle, setDocTitle] = useState("");
  const [docBody, setDocBody] = useState("");
  const uploadDoc = useMutation({
    mutationFn: () => post("/api/knowledge/documents", { title: docTitle, content: docBody, repo_id: repoId || undefined }),
    onSuccess: () => {
      message.success("文档已入库并进入检索索引——生成用例时会自动引用");
      setDocTitle("");
      setDocBody("");
      qc.invalidateQueries({ queryKey: ["wiki"] });
    },
    onError: (e: any) => message.error(e.message),
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
      <Card title="上传知识文档（提升用例生成质量）" size="small" style={{ marginBottom: 16 }}>
        <Input
          placeholder="文档标题（如：支付模块业务规则 / 订单接口约定）"
          value={docTitle}
          onChange={(e) => setDocTitle(e.target.value)}
          style={{ marginBottom: 8 }}
        />
        <Input.TextArea
          rows={4}
          placeholder={"文档正文（markdown）——PRD 片段、接口约定、验收标准、业务规则等。\n入库后进入检索索引，AI 生成用例的引用上下文会自动带上它们。"}
          value={docBody}
          onChange={(e) => setDocBody(e.target.value)}
          style={{ marginBottom: 8 }}
        />
        <Button
          type="primary"
          loading={uploadDoc.isPending}
          disabled={!docTitle.trim() || !docBody.trim()}
          onClick={() => uploadDoc.mutate()}
        >
          入库并索引
        </Button>
      </Card>
      {repoId > 0 && lintQ.data && (
        <Card
          title={`Wiki 体检（${lintQ.data.checked.pages} 页 · ${lintQ.data.findings.length} 项发现）`}
          size="small"
          style={{ marginBottom: 16 }}
        >
          {lintQ.data.findings.length === 0 ? (
            <span style={{ fontSize: 13, color: "var(--tf-ink-3)" }}>无发现——没有 stale/重复/孤儿页，模块覆盖完整。</span>
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {lintQ.data.findings.map((f: any, i: number) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: 13, flexWrap: "wrap" }}>
                  <Tag color={f.severity === "critical" ? "red" : f.severity === "warning" ? "orange" : "blue"} style={{ flexShrink: 0 }}>
                    {f.severity}
                  </Tag>
                  <span style={{ fontWeight: 600 }}>{f.title}</span>
                  <span style={{ color: "var(--tf-ink-3)" }}>{f.detail}</span>
                  {(f.page_ids ?? []).slice(0, 8).map((pid: number) => (
                    <Tag key={pid} style={{ cursor: "pointer" }} onClick={() => openPageById(pid)}>
                      #{pid}
                    </Tag>
                  ))}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
      <Card
        title="代码库 / Wiki（预编译知识层）"
        extra={
          <Space wrap>
            <Button size="small" type="primary" ghost onClick={() => setAskOpen(true)} disabled={!repoId}>
              Wiki 问答
            </Button>
            <select value={repoId} onChange={(e) => setRepoId(Number(e.target.value))} style={{ padding: 4 }}>
              <option value={0}>全部仓库</option>
              {(repos.data ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  {repoName(r.url)}
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
            {(relatedQ.data ?? []).length > 0 && (
              <div style={{ marginTop: 20, paddingTop: 12, borderTop: "1px solid var(--tf-line)" }}>
                <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>相关页面（TF-IDF 互链推荐）</span>
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {relatedQ.data!.map((r) => (
                    <Tag key={r.id} style={{ cursor: "pointer" }} onClick={() => openPageById(r.id)}>
                      {r.title}
                    </Tag>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </Drawer>
      <Drawer
        title="Wiki 问答（检索知识库作答）"
        open={askOpen}
        onClose={() => setAskOpen(false)}
        width={isMobile ? "100%" : 460}
        extra={repoId === 0 ? <Tag color="orange">先选择仓库</Tag> : undefined}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {askMsgs.length === 0 && (
            <Empty
              description={
                <span style={{ fontSize: 13, color: "var(--tf-ink-3)" }}>
                  问点仓库里的事：
                  <br />
                  『订单创建的流程是怎样的？』『密码是怎么校验的？』
                </span>
              }
              style={{ margin: "32px 0" }}
            />
          )}
          {askMsgs.map((m, i) => (
            <div key={i} style={{ display: "grid", gap: 8 }}>
              <div
                style={{
                  alignSelf: "flex-end",
                  background: "var(--tf-acc-soft)",
                  borderRadius: 10,
                  padding: "6px 12px",
                  fontSize: 13,
                  maxWidth: "85%",
                }}
              >
                {m.q}
              </div>
              <div style={{ fontSize: 13.5 }}>
                <Markdown>{m.a}</Markdown>
              </div>
              {m.sources.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>来源：</span>
                  {m.sources.map((s) => (
                    <Tag key={s.id} style={{ cursor: "pointer" }} onClick={() => openPageById(s.id)}>
                      {s.title}
                    </Tag>
                  ))}
                </div>
              )}
            </div>
          ))}
          <Input.TextArea
            value={askQ}
            onChange={(e) => setAskQ(e.target.value)}
            placeholder={repoId === 0 ? "先在上面选择仓库" : "问知识库…（Enter 发送，Shift+Enter 换行）"}
            disabled={!repoId}
            autoSize={{ minRows: 1, maxRows: 4 }}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault();
                ask();
              }
            }}
          />
          <Button type="primary" loading={asking} disabled={!repoId || !askQ.trim()} onClick={ask}>
            发送
          </Button>
        </div>
      </Drawer>
    </div>
  );
}
