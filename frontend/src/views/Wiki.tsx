import { PageHeader } from "../components/PageHeader";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Drawer, Empty, Input, Popconfirm, Space, Table, Tabs, Tag, Tooltip, message } from "antd";
import { AppstoreOutlined, ApartmentOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, del, repoName } from "../api";
import { Markdown } from "../components/Markdown";
import { WikiGraphView, type WikiGraphData } from "../components/WikiGraphView";
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
  updated_at: string;
}

// level 徽章配色对齐互链图谱（WikiGraphView.LEVEL_COLORS）
const LEVEL_META: Record<string, { label: string; color: string }> = {
  repo: { label: "仓库总览", color: "#24272b" },
  module: { label: "模块页", color: "#0d7d72" },
  function: { label: "函数卡片", color: "#0891b2" },
};

/** 卡片视图（OpenWiki WikiBrowseView 移植）：左侧类型筛选 + 搜索 + 卡片网格 */
function PageCard({ page, onClick }: { page: WikiPageRow; onClick: () => void }) {
  const meta = LEVEL_META[page.level] ?? { label: page.level, color: "#6b6f76" };
  return (
    <div
      onClick={onClick}
      style={{
        background: "var(--tf-panel)",
        border: `1px solid ${page.stale ? "#ca8a0440" : "var(--tf-line)"}`,
        borderRadius: 10,
        padding: "12px 14px",
        cursor: "pointer",
        transition: "box-shadow .15s, transform .15s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.boxShadow = "var(--tf-card-shadow)";
        e.currentTarget.style.transform = "translateY(-1px)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.boxShadow = "none";
        e.currentTarget.style.transform = "none";
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: "1px 7px",
              borderRadius: 4,
              color: meta.color,
              background: `${meta.color}15`,
            }}
          >
            {meta.label}
          </span>
          {page.stale && (
            <span style={{ fontSize: 10, fontWeight: 500, padding: "1px 7px", borderRadius: 4, color: "#ca8a04", background: "#ca8a0415" }}>
              ⚠ stale
            </span>
          )}
        </div>
        <span style={{ fontSize: 11, color: "var(--tf-ink-3)" }}>rev {page.rev}</span>
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--tf-ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {page.title}
      </div>
      {(page.module || page.function) && (
        <div style={{ fontSize: 12, color: "var(--tf-ink-3)", marginTop: 3, fontFamily: "Consolas, monospace" }}>
          {[page.module, page.function].filter(Boolean).join(" · ")}
        </div>
      )}
    </div>
  );
}

export function Wiki() {
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const [repoId, setRepoId] = useState(0);
  const [detail, setDetail] = useState<WikiPageRow | null>(null);
  const [viewMode, setViewMode] = useState<"cards" | "graph">("cards");
  const [cardLevel, setCardLevel] = useState<string>("all");
  const [cardSearch, setCardSearch] = useState("");
  const [cardPage, setCardPage] = useState(1);
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  // 有仓库时默认选中第一个：消除问答/重建/体检满屏 disabled 的死首屏
  useEffect(() => {
    if (repoId === 0 && repos.data?.length) setRepoId(repos.data[0].id);
  }, [repos.data, repoId]);
  const pages = useQuery({
    queryKey: ["wiki", repoId],
    queryFn: () => get<WikiPageRow[]>(`/api/wiki${repoId ? `?repo_id=${repoId}` : ""}`),
    refetchInterval: 10000,
  });
  // 互链图谱数据：切到图谱 tab 才拉（TF-IDF 全量计算在后端，约数百页一秒内）
  const graphQ = useQuery({
    queryKey: ["wiki-graph", repoId],
    queryFn: () => get<WikiGraphData>(`/api/wiki/graph${repoId ? `?repo_id=${repoId}` : ""}`),
    enabled: viewMode === "graph",
    staleTime: 60000,
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
  const data = pages.data ?? [];
  const openPageById = (pid: number | string) => {
    const row = data.find((x) => x.id === Number(pid));
    if (row) setDetail(row);
  };

  // 卡片视图：level 筛选 + 标题搜索 + 分页（全部在前端做，795 页无压力）
  const filtered = useMemo(() => {
    const kw = cardSearch.trim().toLowerCase();
    return data.filter(
      (p) =>
        (cardLevel === "all" || p.level === cardLevel) &&
        (!kw || p.title.toLowerCase().includes(kw) || (p.module || "").toLowerCase().includes(kw))
    );
  }, [data, cardLevel, cardSearch]);
  const CARD_PAGE_SIZE = 24;
  const paged = filtered.slice((cardPage - 1) * CARD_PAGE_SIZE, cardPage * CARD_PAGE_SIZE);

  // Wiki 问答（OpenWiki wiki_ask 移植）：检索知识库 → LLM 依据资料作答 → 带来源
  const [askOpen, setAskOpen] = useState(false);
  const [askQ, setAskQ] = useState("");
  const [asking, setAsking] = useState(false);
  const [askMsgs, setAskMsgs] = useState<{ q: string; a: string; sources: { id: number; title: string }[]; saved?: boolean }[]>([]);
  const saveAsk = useMutation({
    // 反幻觉门卫（OpenWiki save_message_as_page 移植）：只有带知识库来源的回答允许入库，
    // 纯 AI 生成的内容禁止污染知识库
    mutationFn: (m: { q: string; a: string; sources: { id: number; title: string }[] }) =>
      post("/api/knowledge/documents", {
        title: `问答：${m.q.slice(0, 40)}`,
        content: `${m.a}\n\n---\n来源页：${m.sources.map((s) => s.title).join("、")}`,
        repo_id: repoId || undefined,
      }),
    onSuccess: (_r, m) => {
      message.success("回答已存为知识文档——生成用例时会自动检索引用");
      setAskMsgs((msgs) => msgs.map((x) => (x.q === m.q && x.a === m.a ? { ...x, saved: true } : x)));
      qc.invalidateQueries({ queryKey: ["knowledge-docs"] });
    },
    onError: (e: any) => message.error(e.message),
  });
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

  const rebuild = useMutation({
    mutationFn: (full: boolean) => post("/api/wiki/rebuild", { repo_id: repoId, full }),
    onSuccess: () => {
      message.success("重建完成");
      qc.invalidateQueries({ queryKey: ["wiki"] });
      qc.invalidateQueries({ queryKey: ["wiki-lint"] });
      qc.invalidateQueries({ queryKey: ["wiki-graph"] });
    },
    onError: (e: any) => message.error(e.message),
  });

  const fixDup = useMutation({
    mutationFn: () => post(`/api/wiki/lint/fix-duplicates?repo_id=${repoId}`),
    onSuccess: (r: any) => {
      message.success(`去重完成：移除 ${r.removed.length} 页，保留 ${r.kept} 页`);
      qc.invalidateQueries({ queryKey: ["wiki"] });
      qc.invalidateQueries({ queryKey: ["wiki-lint"] });
      qc.invalidateQueries({ queryKey: ["wiki-graph"] });
    },
    onError: (e: any) => message.error(e.message),
  });

  const staleCount = data.filter((p) => p.stale).length;

  const wikiCard = (
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
        activeKey={viewMode}
        onChange={(k) => setViewMode(k as typeof viewMode)}
        items={[
          {
            key: "cards",
            label: (
              <span>
                <AppstoreOutlined /> 卡片视图
              </span>
            ),
            children: (
              <div style={{ display: "flex", gap: 16 }}>
                {/* 左侧类型筛选（OpenWiki BrowseView 同款布局） */}
                <div style={{ flexShrink: 0, width: 96, borderRight: "1px solid var(--tf-line)", paddingRight: 12, display: "flex", flexDirection: "column", gap: 2 }}>
                  {[{ id: "all", label: "全部" }, ...Object.entries(LEVEL_META).map(([id, m]) => ({ id, label: m.label }))].map((f) => {
                    const active = cardLevel === f.id;
                    const count = f.id === "all" ? data.length : data.filter((p) => p.level === f.id).length;
                    return (
                      <div
                        key={f.id}
                        onClick={() => {
                          setCardLevel(f.id);
                          setCardPage(1);
                        }}
                        style={{
                          fontSize: 13,
                          padding: "5px 10px",
                          borderRadius: 8,
                          cursor: "pointer",
                          fontWeight: active ? 600 : 400,
                          color: active ? "var(--tf-acc, #0d7d72)" : "var(--tf-ink-2)",
                          background: active ? "var(--tf-acc-soft, rgba(13,125,114,0.08))" : "transparent",
                        }}
                      >
                        {f.label}
                        <span style={{ float: "right", fontSize: 11, opacity: 0.65 }}>{count}</span>
                      </div>
                    );
                  })}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Space style={{ marginBottom: 12, width: "100%", justifyContent: "space-between" }} wrap>
                    <Input.Search
                      placeholder="搜索标题 / 模块"
                      allowClear
                      style={{ width: 260 }}
                      value={cardSearch}
                      onChange={(e) => {
                        setCardSearch(e.target.value);
                        setCardPage(1);
                      }}
                    />
                    <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>{filtered.length} 页</span>
                  </Space>
                  {paged.length === 0 ? (
                    <Empty description="没有匹配的页面" style={{ margin: "48px 0" }} />
                  ) : (
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 10 }}>
                      {paged.map((p) => (
                        <PageCard key={p.id} page={p} onClick={() => setDetail(p)} />
                      ))}
                    </div>
                  )}
                  {filtered.length > CARD_PAGE_SIZE && (
                    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
                      <Button.Group>
                        <Button size="small" disabled={cardPage <= 1} onClick={() => setCardPage((v) => v - 1)}>
                          上一页
                        </Button>
                        <Button size="small" disabled>
                          {cardPage} / {Math.ceil(filtered.length / CARD_PAGE_SIZE)}
                        </Button>
                        <Button size="small" disabled={cardPage >= Math.ceil(filtered.length / CARD_PAGE_SIZE)} onClick={() => setCardPage((v) => v + 1)}>
                          下一页
                        </Button>
                      </Button.Group>
                    </div>
                  )}
                </div>
              </div>
            ),
          },
          {
            key: "graph",
            label: (
              <span>
                <ApartmentOutlined /> 互链图谱
              </span>
            ),
            children: (
              <WikiGraphView
                data={graphQ.data}
                loading={graphQ.isFetching}
                active={viewMode === "graph"}
                onSelectPage={(id) => openPageById(id)}
              />
            ),
          },
        ]}
      />
    </Card>
  );

  return (
    <div>
      <PageHeader title="代码库 / Wiki" subtitle="预编译知识层：卡片 / 图谱 / 表格三视图 · TF-IDF 互链 · 增量重建 · stale 传播（知识文档上传已移至「知识文档」页）" />
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
                  {/* 处置动作闭环：stale 直接可重建，不再只是提示 */}
                  {f.type === "stale" && (
                    <Button size="small" type="link" style={{ padding: 0 }} loading={rebuild.isPending} onClick={() => rebuild.mutate(false)}>
                      重建这批
                    </Button>
                  )}
                  {f.type === "duplicate" && (
                    <Popconfirm title="同标题仅保留最早一页，其余连同检索索引一起删除，确定？" onConfirm={() => fixDup.mutate()}>
                      <Button size="small" type="link" style={{ padding: 0 }} loading={fixDup.isPending}>
                        一键去重
                      </Button>
                    </Popconfirm>
                  )}
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
      {wikiCard}
      <Drawer
        title={detail?.title}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={isMobile ? "100%" : 640}
      >
        {detailQ.data && (
          <>
            <Space style={{ marginBottom: 12 }} wrap>
              <Tag>rev {detailQ.data.rev}</Tag>
              <Tag color={detailQ.data.stale ? "orange" : "green"}>{detailQ.data.stale ? "stale" : "最新"}</Tag>
              {detailQ.data.stale && (
                <Button
                  size="small"
                  type="primary"
                  loading={rebuild.isPending}
                  onClick={() => {
                    rebuild.mutate(false);
                    setDetail(null);
                  }}
                >
                  重建 stale 页（源码已变更，本页内容可能过期）
                </Button>
              )}
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
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                  <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>来源：</span>
                  {m.sources.map((s) => (
                    <Tag key={s.id} style={{ cursor: "pointer" }} onClick={() => openPageById(s.id)}>
                      {s.title}
                    </Tag>
                  ))}
                  {m.saved ? (
                    <Tag color="green">已存为知识文档</Tag>
                  ) : (
                    <Button size="small" type="link" style={{ padding: 0 }} loading={saveAsk.isPending} onClick={() => saveAsk.mutate(m)}>
                      存为知识文档
                    </Button>
                  )}
                </div>
              )}
              {m.sources.length === 0 && (
                <Tooltip title="反幻觉门卫：该回答没有知识库来源支撑，禁止入库，防止 AI 幻觉污染知识库">
                  <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>无知识库来源——不可存入知识库</span>
                </Tooltip>
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
