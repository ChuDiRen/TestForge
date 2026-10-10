import { useEffect, useState } from "react";
import { Button, Card, Input, Space, Table, Tag } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { get, repoName } from "../api";
import { useIsMobile } from "../hooks";

interface SearchHit {
  doc_key: string;
  kind: string;
  title: string;
  excerpt: string;
  score: number;
  repo_id: number;
}

const KIND_META: Record<string, { label: string; color: string }> = {
  req: { label: "需求文档", color: "#7c3aed" },
  plan: { label: "测试计划", color: "#b45309" },
  case: { label: "测试用例", color: "#15803d" },
  defect: { label: "功能缺陷", color: "#c93a2e" },
  user_doc: { label: "知识文档", color: "#0891b2" },
  lesson: { label: "缺陷教训", color: "#be185d" },
  wiki: { label: "技术文档", color: "#0d7d72" },
  function: { label: "代码函数", color: "#57606a" },
};

/** 检索测试台：透明预演生成侧的七路混合检索（原知识资产页内嵌卡，收拢进检索中心）。 */
export function SearchBench() {
  const isMobile = useIsMobile();
  const [repoId, setRepoId] = useState(0);
  const [testQ, setTestQ] = useState("");
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  useEffect(() => {
    if (repoId === 0 && repos.data?.length) setRepoId(repos.data[0].id);
  }, [repos.data, repoId]);

  const searchQ = useQuery({
    queryKey: ["knowledge-search", testQ, repoId],
    queryFn: () =>
      get<SearchHit[]>(`/api/knowledge/search?q=${encodeURIComponent(testQ)}${repoId ? `&repo_id=${repoId}` : ""}`),
    enabled: false,
  });
  const runSearch = () => {
    if (testQ.trim()) searchQ.refetch();
  };

  return (
    <Card
      title="检索测试台"
      extra={
        <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>
          透明预演生成侧的混合检索：需求文档 · 技术文档 · 测试计划 · 测试用例 · 功能缺陷 · 缺陷教训
        </span>
      }
    >
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
      <div style={{ marginBottom: 12, fontSize: 12, color: "var(--tf-ink-3)" }}>
        归属仓库：
        <select value={repoId} onChange={(e) => setRepoId(Number(e.target.value))} style={{ padding: 4, marginLeft: 6 }}>
          <option value={0}>全部仓库</option>
          {(repos.data ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              {repoName(r.url)}
            </option>
          ))}
        </select>
      </div>
      {searchQ.data && (
        <Table<SearchHit>
          rowKey="doc_key"
          size="small"
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          dataSource={searchQ.data}
          locale={{ emptyText: "没有命中——换关键词，或先到「知识资产」上传相关文档" }}
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
  );
}
