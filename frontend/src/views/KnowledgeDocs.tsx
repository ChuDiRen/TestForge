import { PageHeader } from "../components/PageHeader";
import { useEffect, useState } from "react";
import { Button, Card, Empty, Input, Popconfirm, Space, Table, Tag, message } from "antd";
import { DeleteOutlined, FileTextOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, del, repoName } from "../api";
import { useIsMobile } from "../hooks";

interface KnowledgeDoc {
  doc_key: string;
  repo_id: number;
  title: string;
  chars: number;
  updated_at: string;
}
interface SearchHit {
  doc_key: string;
  kind: string;
  title: string;
  excerpt: string;
  score: number;
}

const KIND_META: Record<string, { label: string; color: string }> = {
  req: { label: "需求文档", color: "#7c3aed" },
  plan: { label: "测试计划", color: "#b45309" },
  case: { label: "测试用例", color: "#15803d" },
  defect: { label: "功能缺陷", color: "#c93a2e" },
  user_doc: { label: "知识文档", color: "#0891b2" },
  lesson: { label: "缺陷教训", color: "#be185d" },
  wiki: { label: "技术文档", color: "#0d7d72" },
};

/** 知识文档（用户注入知识的生产与验证闭环）：
 *  上传入 RAG 双索引 → 列表管理 → 检索测试台透明预演「生成时能不能检索到」。 */
export function KnowledgeDocs() {
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const [repoId, setRepoId] = useState(0);
  const [docTitle, setDocTitle] = useState("");
  const [docBody, setDocBody] = useState("");
  const [testQ, setTestQ] = useState("");
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  // 有仓库时默认选中第一个（与 Wiki/图谱页一致，消除初始 disabled）
  useEffect(() => {
    if (repoId === 0 && repos.data?.length) setRepoId(repos.data[0].id);
  }, [repos.data, repoId]);

  const docsQ = useQuery({
    queryKey: ["knowledge-docs", repoId],
    queryFn: () =>
      get<KnowledgeDoc[]>(`/api/knowledge/documents${repoId ? `?repo_id=${repoId}` : ""}`),
  });
  const searchQ = useQuery({
    queryKey: ["knowledge-search", testQ, repoId],
    queryFn: () =>
      get<SearchHit[]>(`/api/knowledge/search?q=${encodeURIComponent(testQ)}${repoId ? `&repo_id=${repoId}` : ""}`),
    enabled: false,
  });

  const uploadDoc = useMutation({
    mutationFn: () => post("/api/knowledge/documents", { title: docTitle, content: docBody, repo_id: repoId || undefined }),
    onSuccess: () => {
      message.success("文档已入库（向量+全文双索引）——AI 生成用例时会自动检索引用");
      setDocTitle("");
      setDocBody("");
      qc.invalidateQueries({ queryKey: ["knowledge-docs"] });
    },
    onError: (e: any) => message.error(e.message),
  });
  const delDoc = useMutation({
    mutationFn: (docKey: string) => del(`/api/knowledge/documents?doc_key=${encodeURIComponent(docKey)}`),
    onSuccess: () => {
      message.success("文档已从检索索引移除");
      qc.invalidateQueries({ queryKey: ["knowledge-docs"] });
    },
    onError: (e: any) => message.error(e.message),
  });

  const runSearch = () => {
    if (!testQ.trim()) return;
    searchQ.refetch();
  };

  return (
    <div>
      <PageHeader title="知识文档" subtitle="用户注入知识：上传入检索索引 → 生成用例自动引用 → 检索测试台验证召回" />
      <Card title="上传知识文档" size="small" style={{ marginBottom: 16 }} extra={repoSelect(repoId, setRepoId, repos.data)}>
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

      <Card title={`已入库文档（${docsQ.data?.length ?? 0} 篇）`} size="small" style={{ marginBottom: 16 }}>
        {(docsQ.data?.length ?? 0) === 0 ? (
          <Empty description="还没有知识文档——上传 PRD 片段、接口约定等，生成用例时会自动引用" style={{ margin: "24px 0" }} />
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {docsQ.data!.map((d) => (
              <div key={d.doc_key} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13 }}>
                <FileTextOutlined style={{ color: "var(--tf-ink-3)", flexShrink: 0 }} />
                <span style={{ fontWeight: 500, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.title}</span>
                <Tag style={{ flexShrink: 0 }}>{d.chars} 字</Tag>
                <span style={{ color: "var(--tf-ink-3)", fontSize: 12, flexShrink: 0 }}>
                  {(d.updated_at || "").slice(0, 16).replace("T", " ")}
                </span>
                <span style={{ flex: 1 }} />
                <Popconfirm title="从检索索引移除该文档？" onConfirm={() => delDoc.mutate(d.doc_key)}>
                  <Button size="small" type="text" danger icon={<DeleteOutlined />} loading={delDoc.isPending && delDoc.variables === d.doc_key} />
                </Popconfirm>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="检索测试台" extra={<span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>透明预演生成侧的混合检索：需求文档 · 技术文档 · 测试计划 · 测试用例 · 功能缺陷 · 缺陷教训</span>}>
        <Space.Compact style={{ width: isMobile ? "100%" : 560, marginBottom: 12 }}>
          <Input
            placeholder="输入生成时可能提出的问题，如：submit_payment 支付"
            value={testQ}
            onChange={(e) => setTestQ(e.target.value)}
            onPressEnter={runSearch}
          />
          <Button type="primary" icon={<SearchOutlined />} loading={searchQ.isFetching} onClick={runSearch}>
            检索
          </Button>
        </Space.Compact>
        {searchQ.data && (
          <Table<SearchHit>
            rowKey="doc_key"
            size="small"
            pagination={false}
            dataSource={searchQ.data}
            locale={{ emptyText: "没有命中——换关键词，或先上传相关文档" }}
            columns={[
              {
                title: "类型",
                dataIndex: "kind",
                width: 100,
                render: (k: string) => {
                  const m = KIND_META[k] ?? { label: k, color: "#6b6f76" };
                  return <Tag color={m.color}>{m.label}</Tag>;
                },
              },
              { title: "标题", dataIndex: "title", ellipsis: true },
              { title: "命中片段", dataIndex: "excerpt", ellipsis: true, render: (v: string) => <span style={{ color: "var(--tf-ink-2)", fontSize: 12.5 }}>{v}</span> },
              { title: "相关度", dataIndex: "score", width: 90, render: (v: number) => <span style={{ fontFamily: "Consolas, monospace", fontSize: 12 }}>{v.toFixed(4)}</span> },
            ]}
          />
        )}
      </Card>
    </div>
  );
}

function repoSelect(value: number, onChange: (v: number) => void, repos: any[] | undefined) {
  return (
    <select value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ padding: 4 }}>
      <option value={0}>全部仓库</option>
      {(repos ?? []).map((r) => (
        <option key={r.id} value={r.id}>
          {repoName(r.url)}
        </option>
      ))}
    </select>
  );
}
