import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Button, Card, Table, Tag, message } from "antd";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";

interface EvalMetrics {
  recall_at_k: number;
  mrr: number;
}
/** detail 行里后端返回的是 [recall, mrr] 元组（dict(zip) 产物），汇总卡是对象——两种形状都兼容 */
const metric = (v: EvalMetrics | [number, number] | undefined, key: "recall" | "mrr"): number | undefined => {
  if (v === undefined) return undefined;
  if (Array.isArray(v)) return key === "recall" ? v[0] : v[1];
  return key === "recall" ? v.recall_at_k : v.mrr;
};
interface EvalDetail {
  query: string;
  relevant: string[];
  hybrid: EvalMetrics | [number, number];
  vector: EvalMetrics | [number, number];
}
interface EvalResp {
  queries: number;
  k: number;
  hybrid?: EvalMetrics;
  vector_only?: EvalMetrics;
  detail?: EvalDetail[];
  message?: string;
}

/** 检索质量评估（rag_eval 闭环的消费端）：黄金集 recall@k / MRR，
 *  混合检索（向量+全文 RRF）vs 向量单路对照——评估结果反哺检索参数调优。 */
export function RagEval({ embedded = false }: { embedded?: boolean } = {}) {
  const [ran, setRan] = useState(false);
  const evalQ = useQuery({
    queryKey: ["rag-eval"],
    queryFn: () => get<EvalResp>("/api/rag/eval?k=5&limit=50"),
    enabled: ran,
    staleTime: 60000,
  });

  const pct = (v: number | undefined) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "-");

  return (
    <div>
      {!embedded && <PageHeader title="检索质量" subtitle="黄金集评测：生成侧混合检索 vs 向量单路的 recall@k / MRR 对照——评估反哺检索调优" />}
      <Card
        title="RAG 检索质量评估"
        extra={
          <Button
            type="primary"
            loading={evalQ.isFetching}
            onClick={() => {
              setRan(true);
              setTimeout(() => evalQ.refetch(), 0);
              message.info("评测运行中：逐条黄金问题跑两路检索");
            }}
          >
            运行评测
          </Button>
        }
      >
        {!ran && (
          <div style={{ textAlign: "center", padding: "28px 0 20px" }}>
            <div style={{ fontSize: 34, marginBottom: 10 }}>🎯</div>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>以已入库用例为黄金集评测两路检索</div>
            <div style={{ fontSize: 12.5, color: "var(--tf-ink-3)", maxWidth: 520, margin: "0 auto 14px" }}>
              评测把每条已入库用例的标题/描述当作黄金问题 → 应命中的用例编码当标准答案，
              分别跑「混合检索（生成侧在用）」和「向量单路」对照 recall@k / MRR。
            </div>
            <div style={{ fontSize: 12.5, color: "var(--tf-ink-2)", marginBottom: 4 }}>① 点上方「运行评测」② 看 recall 差距 ③ 到「系统设置」调检索参数</div>
          </div>
        )}
        {ran && evalQ.isFetching && <span style={{ fontSize: 13, color: "var(--tf-ink-3)" }}>评测运行中…</span>}
        {ran && evalQ.data && evalQ.data.queries === 0 && (
          <span style={{ fontSize: 13, color: "var(--tf-ink-3)" }}>{evalQ.data.message ?? "黄金集为空——先完成一轮生成建库"}</span>
        )}
        {ran && evalQ.data && evalQ.data.queries > 0 && evalQ.data.hybrid && (
          <>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
              <Card size="small" style={{ flex: 1, minWidth: 220 }} styles={{ body: { padding: "10px 14px" } }}>
                <div style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>混合检索（生成侧在用）</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: "#0d7d72", margin: "4px 0" }}>
                  recall@{evalQ.data.k} {pct(evalQ.data.hybrid.recall_at_k)}
                </div>
                <div style={{ fontSize: 12.5 }}>MRR {evalQ.data.hybrid.mrr}</div>
              </Card>
              <Card size="small" style={{ flex: 1, minWidth: 220 }} styles={{ body: { padding: "10px 14px" } }}>
                <div style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>向量单路（对照）</div>
                <div style={{ fontSize: 22, fontWeight: 700, margin: "4px 0" }}>
                  recall@{evalQ.data.k} {pct(evalQ.data.vector_only?.recall_at_k)}
                </div>
                <div style={{ fontSize: 12.5 }}>MRR {evalQ.data.vector_only?.mrr}</div>
              </Card>
              <Card size="small" style={{ flex: 1, minWidth: 220 }} styles={{ body: { padding: "10px 14px" } }}>
                <div style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>黄金问题</div>
                <div style={{ fontSize: 22, fontWeight: 700, margin: "4px 0" }}>{evalQ.data.queries}</div>
                <div style={{ fontSize: 12.5 }}>来自已入库用例</div>
              </Card>
            </div>
            <Table<EvalDetail>
              rowKey="query"
              size="small"
              pagination={false}
              dataSource={evalQ.data.detail ?? []}
              columns={[
                { title: "黄金问题", dataIndex: "query", ellipsis: true },
                {
                  title: "应命中",
                  dataIndex: "relevant",
                  width: 180,
                  render: (v: string[]) => <span style={{ fontFamily: "Consolas, monospace", fontSize: 12 }}>{v.join("、")}</span>,
                },
                {
                  title: "混合 recall",
                  width: 110,
                  render: (_: unknown, r: EvalDetail) => {
                    const v = metric(r.hybrid, "recall");
                    return <Tag color={v !== undefined && v >= 1 ? "green" : v && v > 0 ? "orange" : "red"}>{pct(v)}</Tag>;
                  },
                },
                {
                  title: "向量 recall",
                  width: 110,
                  render: (_: unknown, r: EvalDetail) => <Tag>{pct(metric(r.vector, "recall"))}</Tag>,
                },
              ]}
            />
          </>
        )}
      </Card>
    </div>
  );
}
