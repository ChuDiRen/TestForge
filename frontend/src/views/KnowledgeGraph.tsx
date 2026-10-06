import { PageHeader } from "../components/PageHeader";
import { useEffect, useMemo, useRef, useState } from "react";
import { Card, Col, Drawer, Empty, Row, Select, Space, Table, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import * as echarts from "echarts";
import { get } from "../api";

interface GraphNode {
  id: string;
  name: string;
  category: string;
  module?: string;
  标题?: string;
  状态?: string;
  严重度?: string;
  类别?: string;
  语言?: string;
  度数?: number;
  函数数?: number;
  可测性?: number;
  [k: string]: unknown;
}

interface GraphEdge {
  source: string;
  target: string;
  relation: string;
}

interface GraphData {
  repo: { id: number; url: string };
  nodes: GraphNode[];
  edges: GraphEdge[];
  modules: string[];
  stats: Record<string, number>;
}

const CATEGORIES = ["仓库", "模块", "函数", "需求", "用例", "缺陷"];
// 节点色板对齐品牌系统：仓库墨色 / 模块靛蓝 / 函数紫罗兰 / 需求青 / 用例绿 / 缺陷红
const COLORS = ["#181c2a", "#4f46e5", "#7c3aed", "#0891b2", "#16a34a", "#dc2626"];

/** 知识图谱：节点/边全部来自 /api/graph 真实业务关系 */
export function KnowledgeGraph() {
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  const [repoId, setRepoId] = useState<number | undefined>();
  const [module, setModule] = useState<string>("");
  const [selected, setSelected] = useState<GraphNode | null>(null);

  const graph = useQuery({
    queryKey: ["graph", repoId, module],
    queryFn: () =>
      get<GraphData>(`/api/graph?repo_id=${repoId ?? ""}&module=${encodeURIComponent(module)}&max_functions=120`),
    enabled: repoId !== undefined,
  });

  useEffect(() => {
    if (repoId === undefined && repos.data?.length) setRepoId(repos.data[0].id);
  }, [repos.data, repoId]);

  const chartRef = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts>();

  const option = useMemo(() => {
    const d = graph.data;
    if (!d) return null;
    // 防线：ECharts 遇到重复 id/name 的节点会抛错并卸载整棵 React 树（白屏），先做净化
    const seen = new Set<string>();
    const nodes = d.nodes.filter((n) => {
      const kid = `id:${n.id}`;
      const kname = `name:${n.name}`;
      if (!n.id || !n.name || seen.has(kid) || seen.has(kname)) return false;
      seen.add(kid);
      seen.add(kname);
      return true;
    });
    const nodeIds = new Set(nodes.map((n) => n.id));
    const edges = d.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
    return {
      backgroundColor: "transparent",
      tooltip: {
        formatter: (p: any) =>
          p.dataType === "edge"
            ? `${p.data.sourceName} —[${p.data.relation}]→ ${p.data.targetName}`
            : `<b>${p.data.category}</b> ${p.data.name}`,
      },
      legend: { data: CATEGORIES, textStyle: { fontSize: 11 }, top: 4 },
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          draggable: true,
          data: nodes.map((n) => ({
            id: n.id,
            name: n.name,
            category: CATEGORIES.indexOf(n.category),
            symbolSize:
              n.category === "仓库" ? 46 : n.category === "模块" ? 26 : n.category === "需求" ? 18 : n.category === "缺陷" ? 16 : Math.min(22, 8 + (n.度数 ?? 0) * 2),
            node: n,
            label: { show: n.category !== "函数" || (n.度数 ?? 0) > 0, fontSize: 10 },
          })),
          links: edges.map((e) => ({
            source: e.source,
            target: e.target,
            relation: e.relation,
            sourceName: nodes.find((n) => n.id === e.source)?.name ?? e.source,
            targetName: nodes.find((n) => n.id === e.target)?.name ?? e.target,
            lineStyle: { color: e.relation === "调用" ? "#94a3b8" : "#cbd5e1", width: e.relation === "调用" ? 1.6 : 1, curveness: e.relation === "调用" ? 0.18 : 0.05 },
          })),
          categories: CATEGORIES.map((c, i) => ({ name: c, itemStyle: { color: COLORS[i] } })),
          force: { repulsion: 320, edgeLength: [40, 110], gravity: 0.08 },
          label: { position: "right" },
          emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
          lineStyle: { symbol: ["none", "arrow"], symbolSize: 6 },
        },
      ],
    };
  }, [graph.data]);

  useEffect(() => {
    if (!chartRef.current || !option) return;
    // StrictMode 卸载-重挂载后 chart.current 可能是被 dispose 的旧实例，必须重建
    if (!chart.current || chart.current.isDisposed()) {
      chart.current = echarts.init(chartRef.current);
    }
    chart.current.setOption(option);
    const onClick = (p: any) => {
      if (p.dataType === "node" && p.data.node) setSelected(p.data.node);
    };
    chart.current.on("click", onClick);
    const onResize = () => chart.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.current?.off("click", onClick);
    };
  }, [option]);

  useEffect(
    () => () => {
      chart.current?.dispose();
      chart.current = undefined;
    },
    [],
  );

  const props = selected
    ? Object.entries(selected).filter(([k]) => !["id", "category"].includes(k))
    : [];

  return (
    <div>
      <PageHeader title="知识图谱" subtitle="仓库 · 模块 · 函数调用 · 需求 · 用例 · 缺陷的真实业务关系，节点带影响分与测试域" />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            style={{ width: 260 }}
            placeholder="选择仓库"
            value={repoId}
            onChange={(v) => {
              setRepoId(v);
              setModule("");
            }}
            options={(repos.data ?? []).map((r) => ({ value: r.id, label: `#${r.id} ${String(r.url).split("/").pop()}` }))}
          />
          <Select
            style={{ width: 280 }}
            placeholder="全部模块"
            value={module || undefined}
            allowClear
            onChange={(v) => setModule(v ?? "")}
            options={(graph.data?.modules ?? []).map((m) => ({ value: m, label: m }))}
          />
          {graph.data && (
            <span style={{ fontSize: 12, color: "#666" }}>
              {Object.entries(graph.data.stats)
                .map(([k, v]) => `${k} ${v}`)
                .join(" · ")}
            </span>
          )}
        </Space>
      </Card>
      <Card title="知识图谱（仓库 / 模块 / 函数调用 / 需求 / 用例 / 缺陷 的真实关系）" styles={{ body: { padding: 8 } }}>
        {graph.data ? (
          <div ref={chartRef} style={{ width: "100%", height: 560 }} />
        ) : (
          <Empty description="选择仓库后生成图谱" style={{ margin: "80px 0" }} />
        )}
      </Card>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="节点明细（点击图中节点联动）" size="small">
            {selected ? (
              <Table
                rowKey="k"
                size="small"
                pagination={false}
                dataSource={props.map(([k, v]) => ({ k, v: String(v ?? "-") }))}
                columns={[
                  { title: "属性", dataIndex: "k", width: 120 },
                  { title: "值", dataIndex: "v", ellipsis: true },
                ]}
              />
            ) : (
              <Empty description="点击图中任意节点查看属性" style={{ margin: "24px 0" }} />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="图例与关系类型" size="small">
            <Space wrap>
              {CATEGORIES.map((c, i) => (
                <Tag key={c} color={COLORS[i]}>
                  {c}
                </Tag>
              ))}
            </Space>
            <Table
              style={{ marginTop: 12 }}
              rowKey="r"
              size="small"
              pagination={false}
              dataSource={[
                { r: "包含", d: "仓库 → 模块 → 函数（索引结构）" },
                { r: "调用", d: "函数 → 函数（tree-sitter 调用图）" },
                { r: "派生", d: "需求 → 用例（source_req 溯源）" },
                { r: "覆盖", d: "用例 → 被测函数（target_function）" },
                { r: "暴露", d: "用例 → 缺陷（沙箱失败自动关联）" },
              ]}
              columns={[
                { title: "关系", dataIndex: "r", width: 90 },
                { title: "含义", dataIndex: "d" },
              ]}
            />
          </Card>
        </Col>
      </Row>
      <Drawer title={selected ? `${selected.category} · ${selected.name}` : ""} open={!!selected} onClose={() => setSelected(null)} width="60%">
        {selected && (
          <Table
            rowKey="k"
            size="small"
            pagination={false}
            dataSource={Object.entries(selected)
              .filter(([k]) => k !== "id")
              .map(([k, v]) => ({ k, v: typeof v === "object" ? JSON.stringify(v) : String(v ?? "-") }))}
            columns={[
              { title: "属性", dataIndex: "k", width: 140 },
              { title: "值", dataIndex: "v", ellipsis: true },
            ]}
          />
        )}
      </Drawer>
    </div>
  );
}
