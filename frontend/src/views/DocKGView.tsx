import { useCallback, useEffect, useRef, useState } from "react";
import { AutoComplete, Button, Card, Drawer, Empty, Input, InputNumber, message, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip } from "antd";
import { AimOutlined, DownloadOutlined, MinusOutlined, PlusOutlined, RedoOutlined, ScissorOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import Graph from "graphology";
import Sigma from "sigma";
import EdgeCurveProgram from "@sigma/edge-curve";
import FA2Layout from "graphology-layout-forceatlas2/worker";
import noverlap from "graphology-layout-noverlap";
import { del as apiDel, downloadB64, get, post } from "../api";
import { useThemeMode } from "../hooks";
import { useLang } from "../i18n";

interface KgNode {
  id: string;
  label: string;
  etype: string;
  degree: number;
  sources: number;
}
interface KgEdge {
  id: number;
  source: string;
  target: string;
  rtype: string;
  weight: number;
}
interface GraphData {
  nodes: KgNode[];
  edges: KgEdge[];
  focus?: string | null;
}

const TYPE_COLORS: Record<string, string> = {
  module: "#2563eb",
  function: "#0d9488",
  concept: "#7c3aed",
  risk: "#dc2626",
  flow: "#ea580c",
  contract: "#0891b2",
};
const COMMUNITY_PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#9333ea", "#e11d48"];

/** 文档知识图谱查看器（LightRAG WebUI Graph 对齐）：label 过滤 / 类型图例 / 社区着色 /
 *  focus 展开邻居 / 实体编辑（改名/合并/删除） / 图数据导出 / 节点上限。 */
export function DocKGView() {
  const { t } = useLang();
  const themeMode = useThemeMode();
  const [workspace, setWorkspace] = useState("default");
  const [labelFilter, setLabelFilter] = useState("");
  const [maxNodes, setMaxNodes] = useState(400);
  const [typeFilter, setTypeFilter] = useState<Record<string, boolean>>({});
  const [communityMode, setCommunityMode] = useState(false);
  const [focus, setFocus] = useState("");
  const [selected, setSelected] = useState<KgNode | null>(null);
  const [renameTo, setRenameTo] = useState("");

  const containerRef = useRef<HTMLDivElement | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const hoverRef = useRef<string | null>(null);
  const layoutRef = useRef<FA2Layout | null>(null);
  const themeModeRef = useRef(themeMode);
  themeModeRef.current = themeMode;

  const wsQ = useQuery({ queryKey: ["kg-workspaces"], queryFn: () => get<string[]>("/api/kg/workspaces") });
  const graphQ = useQuery({
    queryKey: ["kg-graph", workspace, labelFilter, maxNodes, focus],
    queryFn: () =>
      get<GraphData>(
        `/api/kg/graph?workspace=${encodeURIComponent(workspace)}&max_nodes=${maxNodes}&search=${encodeURIComponent(labelFilter)}&focus=${encodeURIComponent(focus)}&depth=1`,
      ),
  });
  const commQ = useQuery({
    queryKey: ["kg-communities", workspace],
    queryFn: () => get<{ cluster_id: number; members: string[] }[]>(`/api/kg/communities?workspace=${encodeURIComponent(workspace)}&level=1`),
    enabled: communityMode,
  });

  const communityOf = useCallback(
    (name: string): number => {
      if (!commQ.data) return -1;
      for (const c of commQ.data) if (c.members.includes(name)) return c.cluster_id;
      return -1;
    },
    [commQ.data],
  );

  // 重建 graphology + Sigma 数据
  useEffect(() => {
    const data = graphQ.data;
    if (!data || !sigmaRef.current || !graphRef.current) return;
    const g = graphRef.current;
    g.clear();
    data.nodes.forEach((n, i) => {
      const angle = (i * 2 * Math.PI * 0.618) % (2 * Math.PI);
      const radius = 30 + ((i * 89) % 300);
      const color = communityMode && communityOf(n.id) >= 0 ? COMMUNITY_PALETTE[communityOf(n.id) % COMMUNITY_PALETTE.length] : TYPE_COLORS[n.etype] ?? "#6b7280";
      g.addNode(n.id, {
        label: n.label,
        x: radius * Math.cos(angle) + ((i % 5) - 2) * 6,
        y: radius * Math.sin(angle) + ((i % 3) - 1) * 6,
        size: Math.min(9, 2.5 + n.degree * 0.7),
        color,
        hidden: typeFilter[n.etype] === false,
        kgnode: n,
      });
    });
    data.edges.forEach((e) => {
      if (!g.hasNode(e.source) || !g.hasNode(e.target) || g.hasEdge(e.source, e.target)) return;
      g.addEdge(e.source, e.target, { size: Math.min(2.5, 0.6 + e.weight * 0.2), color: themeMode === "dark" ? "#3f4753" : "#c8ccd2", label: e.rtype, _edge: e });
    });
    // FA2 supervisor 收敛后停掉（静态布局）
    layoutRef.current?.stop();
    layoutRef.current?.kill();
    layoutRef.current = null;
    try {
      const layout = new FA2Layout(g, { settings: { gravity: 1.2, adjustSizes: true, barnesHutOptimize: true } });
      layoutRef.current = layout;
      layout.start();
      window.setTimeout(() => {
        layoutRef.current?.stop();
        layoutRef.current?.kill();
        layoutRef.current = null;
        try {
          noverlap.assign(g, { maxIterations: 60 });
        } catch {
          /* 单节点等退化图忽略 */
        }
        sigmaRef.current?.refresh();
      }, 900);
    } catch {
      /* 小图布局可失败 */
    }
    sigmaRef.current.refresh({ skipIndexation: false });
  }, [graphQ.data, themeMode, typeFilter, communityMode, communityOf]);

  // Sigma 初始化（一次）
  useEffect(() => {
    if (!containerRef.current || sigmaRef.current) return;
    const g = new Graph({ multi: false, type: "undirected" });
    graphRef.current = g;
    sigmaRef.current = new Sigma(g, containerRef.current, {
      allowInvalidContainer: true,
      defaultEdgeType: "curve",
      edgeProgramClasses: { curve: EdgeCurveProgram },
      renderEdgeLabels: false,
      labelDensity: 1.2,
      labelGridCellSize: 90,
      minCameraRatio: 0.05,
      maxCameraRatio: 12,
      defaultNodeColor: "#6b7280",
      labelColor: { color: themeMode === "dark" ? "#d7dbe0" : "#33383f" },
      nodeReducer: (node, attrs) => {
        const hover = hoverRef.current;
        if (hover && hover !== node && !(graphRef.current?.areNeighbors(node, hover) ?? false)) {
          return { ...attrs, label: "", color: themeModeRef.current === "dark" ? "#2a2f37" : "#e4e6ea" };
        }
        return attrs;
      },
    });
    sigmaRef.current.on("clickNode", ({ node }) => {
      const attrs = g.getNodeAttributes(node) as { kgnode: KgNode };
      setSelected(attrs.kgnode);
      setRenameTo(attrs.kgnode.label);
    });
    sigmaRef.current.on("enterNode", ({ node }) => {
      hoverRef.current = node;
      sigmaRef.current?.refresh();
    });
    sigmaRef.current.on("leaveNode", () => {
      hoverRef.current = null;
      sigmaRef.current?.refresh();
    });
    return () => {
      sigmaRef.current?.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const zoom = (dir: 1 | -1) => {
    const cam = sigmaRef.current?.getCamera();
    cam?.animatedZoom({ duration: 180, factor: dir === 1 ? 1.4 : 1 / 1.4 });
  };

  const relsOfSelected = graphQ.data?.edges.filter((e) => e.source === selected?.id || e.target === selected?.id) ?? [];

  const doRename = async () => {
    if (!selected || !renameTo.trim() || renameTo === selected.id) return;
    try {
      await post("/api/kg/entity", { name: selected.id, new_name: renameTo.trim(), workspace });
      message.success(t.docgraph.rename);
      setSelected(null);
      graphQ.refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };
  const doDelete = async () => {
    if (!selected) return;
    try {
      await apiDel(`/api/kg/entity?name=${encodeURIComponent(selected.id)}&workspace=${encodeURIComponent(workspace)}`);
      message.success(t.docgraph.del);
      setSelected(null);
      graphQ.refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };
  const doMerge = async () => {
    if (!selected) return;
    let into = "";
    // 简易输入：用 AutoComplete 已有的 renameTo 之外的第二个目标不引入 —— 用 prompt 语义改为：rename 即可，合并走「改名到已存在实体」自动合并（后端语义）
    into = renameTo.trim();
    if (!into || into === selected.id) return;
    try {
      await post("/api/kg/entity/merge", { sources: [selected.id], into, workspace });
      message.success(t.docgraph.merge);
      setSelected(null);
      graphQ.refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };
  const doExport = async (fmt: string) => {
    try {
      const r = await get<{ filename: string; content_b64: string }>(`/api/kg/export?what=graph&fmt=${fmt}&workspace=${encodeURIComponent(workspace)}`);
      downloadB64(r.filename, r.content_b64);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const types = [...new Set((graphQ.data?.nodes ?? []).map((n) => n.etype))];
  const dark = themeMode === "dark";

  return (
    <Card
      size="small"
      bodyStyle={{ padding: 0, position: "relative", height: "calc(100vh - 210px)", minHeight: 480 }}
      title={
        <Space wrap size={10}>
          <Select value={workspace} onChange={(v) => { setWorkspace(v); setFocus(""); }} size="small" style={{ minWidth: 130 }}
            options={[...new Set(["default", ...(wsQ.data ?? [])])].map((w) => ({ value: w, label: w }))} />
          <AutoComplete
            size="small"
            style={{ width: 190 }}
            value={labelFilter}
            onChange={setLabelFilter}
            placeholder={t.docgraph.searchPh}
            options={(graphQ.data?.nodes ?? []).slice(0, 8).map((n) => ({ value: n.label }))}
          />
          <Select size="small" value={t.docgraph.typeAll} style={{ width: 110 }} disabled
            options={[]} />
          <Tooltip title={t.docgraph.communities}>
            <Switch size="small" checked={communityMode} onChange={setCommunityMode} />
          </Tooltip>
          <InputNumber size="small" min={50} max={2000} step={100} value={maxNodes} onChange={(v) => setMaxNodes(Number(v) || 400)} addonBefore={t.docgraph.maxNodes} style={{ width: 170 }} />
          <Space size={4}>
            <Button size="small" icon={<DownloadOutlined />} onClick={() => doExport("json")}>JSON</Button>
            <Button size="small" icon={<DownloadOutlined />} onClick={() => doExport("graphml")}>GraphML</Button>
          </Space>
        </Space>
      }
      extra={
        <Space size={4}>
          <Tooltip title={focus ? `focus: ${focus}（depth=1）` : ""}>
            <Button size="small" type={focus ? "primary" : "text"} icon={<AimOutlined />} disabled={!focus} onClick={() => setFocus("")} />
          </Tooltip>
          <Button size="small" type="text" icon={<PlusOutlined />} onClick={() => zoom(1)} />
          <Button size="small" type="text" icon={<MinusOutlined />} onClick={() => zoom(-1)} />
          <Button size="small" type="text" icon={<RedoOutlined />} onClick={() => graphQ.refetch()} />
        </Space>
      }
    >
      <div ref={containerRef} style={{ width: "100%", height: "100%", background: dark ? "#191c21" : "#f7f8fa" }} />
      {/* 类型图例（点击开关） */}
      <div style={{ position: "absolute", left: 12, bottom: 12, display: "flex", gap: 6, flexWrap: "wrap", maxWidth: "70%" }}>
        {types.map((tp) => (
          <Tag
            key={tp}
            style={{ cursor: "pointer", opacity: typeFilter[tp] === false ? 0.35 : 1, background: dark ? "#22262d" : "#fff" }}
            onClick={() => setTypeFilter((s) => ({ ...s, [tp]: s[tp] === false }))}
          >
            <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: TYPE_COLORS[tp] ?? "#6b7280", marginRight: 5 }} />
            {tp}
          </Tag>
        ))}
      </div>
      {(graphQ.data?.nodes.length ?? 0) === 0 && !graphQ.isLoading && (
        <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", pointerEvents: "none" }}>
          <Empty description={t.docgraph.empty} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </div>
      )}

      <Drawer
        title={<span>{selected?.label}</span>}
        width={380}
        open={!!selected}
        onClose={() => setSelected(null)}
        extra={
          <Space size={4}>
            <Popconfirm title={`${t.docgraph.del} "${selected?.label}"?`} onConfirm={doDelete}>
              <Button size="small" danger icon={<ScissorOutlined />} />
            </Popconfirm>
          </Space>
        }
      >
        {selected && (
          <div style={{ display: "grid", gap: 12 }}>
            <Space wrap>
              <Tag color={TYPE_COLORS[selected.etype] ?? "default"}>{selected.etype}</Tag>
              <Tag>{t.docgraph.degree}: {selected.degree}</Tag>
              <Tag>{t.docgraph.sources}: {selected.sources}</Tag>
            </Space>
            <Space.Compact style={{ width: "100%" }}>
              <Input size="small" value={renameTo} onChange={(e) => setRenameTo(e.target.value)} />
              <Button size="small" onClick={doRename}>{t.docgraph.rename}</Button>
              <Tooltip title={t.docgraph.merge}>
                <Button size="small" onClick={doMerge}>⇒</Button>
              </Tooltip>
            </Space.Compact>
            <Button size="small" block onClick={() => { setFocus(selected.id); setSelected(null); }} icon={<AimOutlined />}>
              {t.docgraph.expand}
            </Button>
            <Table<KgEdge>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={relsOfSelected}
              locale={{ emptyText: "—" }}
              columns={[
                { title: "relation", render: (_: unknown, e: KgEdge) => `${e.source} -${e.rtype}-> ${e.target}` , ellipsis: true },
                { title: "w", dataIndex: "weight", width: 40, render: (v: number) => v.toFixed(0) },
              ]}
            />
          </div>
        )}
      </Drawer>
    </Card>
  );
}
