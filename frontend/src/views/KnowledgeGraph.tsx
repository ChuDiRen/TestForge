import { PageHeader } from "../components/PageHeader";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AutoComplete, Button, Card, Divider, Drawer, Empty, message, Popover, Select, Space, Table, Tag, Tooltip } from "antd";
import { AimOutlined, DragOutlined, MinusOutlined, PlusOutlined, QuestionCircleOutlined, RedoOutlined, RestOutlined, ScissorOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import Graph from "graphology";
import Sigma from "sigma";
import EdgeCurveProgram from "@sigma/edge-curve";
import FA2Layout from "graphology-layout-forceatlas2/worker";
import noverlap from "graphology-layout-noverlap";
import { get, repoName } from "../api";
import { useThemeMode } from "../hooks";

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
  meta?: { head_rev?: string; last_pull?: string | null; indexed_functions?: number };
  nodes: GraphNode[];
  edges: GraphEdge[];
  modules: string[];
  stats: Record<string, number>;
}

/** 索引新鲜度（GitNexus staleness 思路的轻量版）：HEAD 短 rev + 拉取相对时间 */
function relTime(iso: string | null | undefined): string {
  if (!iso) return "未知";
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

const CATEGORIES = ["仓库", "模块", "函数", "需求", "用例", "缺陷"];
// 节点色板对齐品牌系统：仓库墨色 / 模块杉青 / 函数深杉青 / 需求青 / 用例绿 / 缺陷红
const COLORS = ["#24272b", "#0d7d72", "#0b655c", "#0891b2", "#15803d", "#c93a2e"];
// 函数层按模块着色（GitNexus COMMUNITY_COLORS 同款 12 色轮换）：top 模块各占一色、
// 其余灰——打破 600 函数一坨深绿的视觉混沌，模块聚类直接可见
const MODULE_COLORS = ["#0891b2", "#0d7d72", "#15803d", "#b45309", "#7c3aed", "#be185d", "#4f46e5", "#a16207", "#0e7490", "#6d28d9", "#c2410c", "#334155"];
const FUNC_FALLBACK_COLOR = "#9aa0a8";
// 自闭环默认视图：接入仓库的代码结构；需求/用例/缺陷为下游溯源资产，点图例叠加
// GitNexus 同款：力导默认只看代码调用图（函数层），层级/溯源资产点图例叠加
const DEFAULT_HIDDEN = ["仓库", "模块", "需求", "用例", "缺陷"];

// ECharts/Sigma 画布吃不到 CSS 变量，按当前主题解析出实际色值
const cssVar = (name: string, fallback: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

// FA2 参数按节点规模分档（对齐 GitNexus getFA2Settings）：
// 大图降 gravity / 提 scalingRatio 让连通分量充分展开，避免 600 函数挤成一坨
function fa2Settings(n: number) {
  if (n < 100) return { gravity: 0.8, scalingRatio: 15, slowDown: 1, barnesHutOptimize: false, theta: 0.6 };
  if (n < 300) return { gravity: 0.18, scalingRatio: 120, slowDown: 2, barnesHutOptimize: true, theta: 0.8 };
  if (n < 1000) return { gravity: 0.06, scalingRatio: 300, slowDown: 4, barnesHutOptimize: true, theta: 0.8 };
  return { gravity: 0.04, scalingRatio: 420, slowDown: 6, barnesHutOptimize: true, theta: 0.8 };
}
// LightRAG 同款时间预算思路：布局是给用户看的，不是跑精确模拟——短收敛 + noverlap 收尾
const FA2_DURATION = (n: number) => (n < 150 ? 6000 : n < 350 ? 10000 : 14000);

interface SigmaRefs {
  graph: Graph;
  sigma: Sigma;
  layout: FA2Layout | null;
  selected: string | null;
  hover: string | null;
  blast: Set<string> | null;
  cycles: Set<string> | null;
  changes: Set<string> | null;
  processChain: Set<string> | null;
  colors: { ink: string; ink2: string; panel: string; line: string; dim: string };
}

/** 知识图谱：Sigma.js/Graphology WebGL 渲染（对齐 GitNexus GraphCanvas 效果）。
 *  节点/边全部来自 /api/graph 真实业务关系；FA2 worker 力导 + 标签密度控制 +
 *  邻居高亮暗化 + 相机动画聚焦 + 搜索定位 + 同心圆布局 + 影响半径（blast radius）。 */
export function KnowledgeGraph({ embedded = false }: { embedded?: boolean } = {}) {
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  const [repoId, setRepoId] = useState<number | undefined>();
  const [module, setModule] = useState<string>("");
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [hiddenCats, setHiddenCats] = useState<Record<string, boolean>>(
    () => Object.fromEntries(CATEGORIES.map((c) => [c, DEFAULT_HIDDEN.includes(c)]))
  );
  const [layoutMode, setLayoutMode] = useState<"force" | "circles">("force");
  const [blastOn, setBlastOn] = useState(false);
  const [searching, setSearching] = useState("");
  const themeMode = useThemeMode();

  const graph = useQuery({
    queryKey: ["graph", repoId, module],
    queryFn: () =>
      get<GraphData>(`/api/graph?repo_id=${repoId ?? ""}&module=${encodeURIComponent(module)}&max_functions=600`),
    enabled: repoId !== undefined,
  });

  useEffect(() => {
    if (repoId === undefined && repos.data?.length) setRepoId(repos.data[0].id);
  }, [repos.data, repoId]);

  // 影响半径（blast radius）：受选中函数变更影响的调用方集合
  const blast = useQuery({
    queryKey: ["impact", selected?.name],
    queryFn: () => get<{ function: string; affected: { name: string; depth: number; confidence: number }[]; certainty?: string; basis?: string }>(
      `/api/functions/${encodeURIComponent(selected!.name)}/impact`
    ),
    enabled: blastOn && !!selected && selected.category === "函数",
  });

  // 循环依赖（GitNexus check）与未提交变更（detect_changes）的高亮状态
  const [cycleOn, setCycleOn] = useState(false);
  const [changesOn, setChangesOn] = useState(false);
  const cyclesQ = useQuery({
    queryKey: ["cycles", repoId],
    queryFn: () => get<{ total: number; cycles: { members: string[]; size: number }[] }>(`/api/repos/${repoId}/cycles`),
    enabled: repoId !== undefined,
  });
  const changesQ = useQuery({
    queryKey: ["changes", repoId],
    queryFn: () =>
      get<{ scope: string; changed_files: number; affected_functions: number; functions: { name: string; module: string; risk: number; direct_callers: string[] }[] }>(
        `/api/repos/${repoId}/changes?scope=all`
      ),
    enabled: false,
  });

  // 执行流（GitNexus Processes 移植）：入口→传递→出口的业务旅程，链上节点紫色高亮
  const [processOpen, setProcessOpen] = useState(false);
  const [processOn, setProcessOn] = useState(false);
  const [activeProcess, setActiveProcess] = useState<{ entry: string; chain: string[]; exits: string[] } | null>(null);
  const processesQ = useQuery({
    queryKey: ["processes", repoId],
    queryFn: () => get<{ processes: { entry: string; chain: string[]; modules: string[]; exits: string[]; depth: number }[]; total: number }>(
      `/api/repos/${repoId}/processes?max_processes=10`
    ),
    enabled: repoId !== undefined && processOpen,
  });
  const highlightProcess = (chain: string[] | null) => {
    const r = R.current;
    if (!r) return;
    setProcessOn(!!chain);
    setActiveProcess(chain ? { entry: chain[0], chain, exits: (processesQ.data?.processes.find((p) => p.chain === chain))?.exits ?? [] } : null);
    r.processChain = chain ? new Set(chain) : null;
    r.sigma.refresh();
  };

  // ── 净化后的图数据（重复 id/name 会让渲染层崩，先过滤）──
  const clean = useMemo(() => {
    const d = graph.data;
    if (!d) return null;
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
    // 孤立判定与布局投影同源：只用语义边（调用/派生/覆盖/暴露）——
    // 「包含」边把每个函数都连到模块上，算进去孤立数永远是 0
    const edges = d.edges.filter((e) => e.relation !== "包含" && nodeIds.has(e.source) && nodeIds.has(e.target));
    const linked = new Set<string>();
    for (const e of edges) {
      linked.add(e.source);
      linked.add(e.target);
    }
    const isolates = new Set(nodes.filter((n) => n.category === "函数" && !linked.has(n.id)).map((n) => n.name));
    const allEdges = d.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
    // 函数层按模块着色：调用频次 top12 模块各占一色（GitNexus 社区着色语义），其余灰
    const moduleFreq = new Map<string, number>();
    for (const n of nodes) {
      if (n.category === "函数" && n.module) moduleFreq.set(n.module, (moduleFreq.get(n.module) ?? 0) + 1);
    }
    const moduleColor = new Map<string, string>(
      [...moduleFreq.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, MODULE_COLORS.length)
        .map(([m, _c], i) => [m, MODULE_COLORS[i]])
    );
    return { nodes, edges, allEdges, isolates, moduleColor };
  }, [graph.data]);

  const nodeById = useMemo(() => new Map((clean?.nodes ?? []).map((n) => [n.id, n])), [clean]);

  // ── Sigma 实例：容器挂载后初始化（数据/主题/交互通过 ref + refresh 应用）──
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [canvasReady, setCanvasReady] = useState(false);
  const initRef = useCallback((el: HTMLDivElement | null) => {
    containerRef.current = el;
    setCanvasReady(!!el);
  }, []);
  const R = useRef<SigmaRefs | null>(null);
  const hiddenRef = useRef<Record<string, boolean>>(hiddenCats);
  hiddenRef.current = hiddenCats;
  const [showIsolates, setShowIsolates] = useState(false);
  const showIsolatesRef = useRef(showIsolates);
  showIsolatesRef.current = showIsolates;
  const isolatesRef = useRef<Set<string>>(clean?.isolates ?? new Set());
  isolatesRef.current = clean?.isolates ?? new Set();
  const [tick, setTick] = useState(0); // 布局运行指示

  const themeColors = useCallback(
    () => ({
      ink: cssVar("--tf-ink", "#24272b"),
      ink2: cssVar("--tf-ink-2", "#6b6f76"),
      panel: cssVar("--tf-panel", "#ffffff"),
      line: cssVar("--tf-line-strong", "#e2dfd8"),
      dim: cssVar("--tf-bg", "#f7f7f5"),
    }),
    []
  );

  const dimColor = (color: string, alpha: number, bg: string) => {
    const hex = (c: string) => {
      const m = c.replace("#", "");
      const v = m.length === 3 ? m.split("").map((x) => x + x).join("") : m.slice(0, 6);
      return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
    };
    const a = hex(color);
    const b = hex(bg);
    const mix = a.map((x, i) => Math.round(x + (b[i] - x) * (1 - alpha)));
    return `#${mix.map((x) => x.toString(16).padStart(2, "0")).join("")}`;
  };

  const nodeSize = (n: GraphNode) =>
    n.category === "仓库" ? 14
    : n.category === "模块" ? 8
    : n.category === "需求" ? 7
    : n.category === "缺陷" ? 6
    : n.category === "用例" ? 5
    : Math.min(7, 2.5 + (n.度数 ?? 0) * 0.6); // 函数层压小：密集区减少重叠，度数做视觉层级

  const buildGraph = useCallback((data: { nodes: GraphNode[]; edges: GraphEdge[]; moduleColor: Map<string, string> }) => {
    const g = new Graph({ multi: false, type: "directed" });
    const N = data.nodes.length;
    data.nodes.forEach((n, i) => {
      // 均匀随机播种：同心环播种会被 FA2 原样保留成"鬼圆环"（低度节点挪不动）
      const angle = (i * 2 * Math.PI * 0.618) % (2 * Math.PI);
      const radius = 40 + ((i * 97) % 400);
      const color =
        n.category === "函数"
        ? (n.module && data.moduleColor.get(n.module)) || FUNC_FALLBACK_COLOR
        : COLORS[CATEGORIES.indexOf(n.category)] ?? COLORS[0];
      g.addNode(n.id, {
        label: n.name,
        x: radius * Math.cos(angle) + ((i % 7) - 3) * 8,
        y: radius * Math.sin(angle) + ((i % 5) - 2) * 8,
        size: nodeSize(n),
        color,
        hidden: false,
        highlighted: false,
        node: n,
      });
    });
    void N;
    data.edges.forEach((e, i) => {
      // GitNexus 的布局投影只保留语义边（调用/派生/覆盖/暴露）：
      // 包含（仓库→模块→函数）边会造成星型辐射，把枢纽函数全部压进画布中心
      if (e.relation === "包含") return;
      if (!g.hasNode(e.source) || !g.hasNode(e.target) || g.hasEdge(e.source, e.target)) return;
      g.addDirectedEdge(e.source, e.target, {
        relation: e.relation,
        size: e.relation === "调用" ? 1.2 : 0.7,
        zIndex: e.relation === "调用" ? 1 : 0,
        _i: i,
      });
    });
    return g;
  }, []);

  // 初始化 Sigma（容器挂载后执行一次）
  useEffect(() => {
    if (!canvasReady || !containerRef.current || R.current) return;
    const colors = themeColors();
    const g = new Graph({ multi: false, type: "directed" });
    const sigma = new Sigma(g, containerRef.current, {
      renderLabels: true,
      labelFont: "Consolas, DengXian, monospace",
      labelSize: 11,
      labelWeight: "500",
      labelColor: { color: colors.ink },
      labelRenderedSizeThreshold: 7,
      labelDensity: 0.16,
      labelGridCellSize: 80,
      defaultNodeColor: COLORS[0],
      // 8 位 hex 带 50% 透明：1798 条调用边默认退后，hover 邻接边才提亮
      defaultEdgeColor: colors.line + "80",
      defaultEdgeType: "curved",
      edgeProgramClasses: { curved: EdgeCurveProgram },
      minCameraRatio: 0.02,
      maxCameraRatio: 20,
      hideEdgesOnMove: true,
      zIndex: true,
      allowInvalidContainer: true,
      nodeReducer: (node, data) => {
        const r = R.current;
        if (!r) return data;
        const attrs: Record<string, unknown> = { ...data };
        const n = data.node as GraphNode | undefined;
        if (n && hiddenRef.current[n.category]) attrs.hidden = true;
        if (!showIsolatesRef.current && isolatesRef.current.has(String(data.label))) attrs.hidden = true;
        // 执行流：链上节点紫色（GitNexus processes 图层）
        if (r.processChain) {
          if (r.processChain.has(String(data.label))) {
            attrs.color = "#7c3aed";
            attrs.size = (data.size as number) * 1.4;
            attrs.highlighted = true;
            attrs.zIndex = 5;
          } else {
            attrs.color = dimColor(String(data.color), 0.85, r.colors.dim);
          }
          return attrs;
        }
        if (r.blast) {
          if (r.blast.has(String(data.label))) {
            attrs.color = "#e5645a";
            attrs.size = (data.size as number) * 1.6;
            attrs.highlighted = true;
            attrs.zIndex = 5;
          } else {
            attrs.color = dimColor(String(data.color), 0.85, r.colors.dim);
            attrs.size = (data.size as number) * 0.5;
            attrs.hidden = attrs.hidden || false;
          }
          return attrs;
        }
        // 循环依赖：成环节点红色，其余暗化（GitNexus check）
        if (r.cycles) {
          if (r.cycles.has(String(data.label))) {
            attrs.color = "#e5645a";
            attrs.size = (data.size as number) * 1.35;
            attrs.highlighted = true;
            attrs.zIndex = 4;
          } else {
            attrs.color = dimColor(String(data.color), 0.85, r.colors.dim);
          }
          return attrs;
        }
        // 未提交变更：波及函数橙色（GitNexus detect_changes）
        if (r.changes) {
          if (r.changes.has(String(data.label))) {
            attrs.color = "#dfa050";
            attrs.size = (data.size as number) * 1.25;
            attrs.highlighted = true;
            attrs.zIndex = 3;
          } else {
            attrs.color = dimColor(String(data.color), 0.85, r.colors.dim);
          }
          return attrs;
        }
        const focus = r.hover || r.selected;
        if (focus) {
          const isFocus = node === focus;
          const isNeighbor = r.graph.hasEdge(node, focus) || r.graph.hasEdge(focus, node);
          if (isFocus) {
            attrs.size = (data.size as number) * 1.5;
            attrs.highlighted = true;
            attrs.zIndex = 4;
          } else if (isNeighbor) {
            attrs.size = (data.size as number) * 1.15;
            attrs.zIndex = 2;
          } else {
            attrs.color = dimColor(String(data.color), 0.8, r.colors.dim);
            attrs.zIndex = 0;
          }
        }
        return attrs;
      },
      edgeReducer: (edge, data) => {
        const r = R.current;
        if (!r) return data;
        const attrs: Record<string, unknown> = { ...data };
        // 执行流：链上相邻边紫色加粗
        if (r.processChain) {
          const [s, t] = r.graph.extremities(edge);
          const sn = r.graph.getNodeAttribute(s, "label");
          const tn = r.graph.getNodeAttribute(t, "label");
          const onChain = r.processChain.has(String(sn)) && r.processChain.has(String(tn));
          attrs.hidden = !onChain;
          if (onChain) {
            attrs.color = "#7c3aed";
            attrs.size = (data.size as number) * 2.5;
            attrs.zIndex = 3;
          }
          return attrs;
        }
        const focus = r.hover || r.selected;
        if (r.blast) {
          const [s, t] = r.graph.extremities(edge);
          const sn = r.graph.getNodeAttribute(s, "label");
          const tn = r.graph.getNodeAttribute(t, "label");
          attrs.hidden = !(r.blast.has(String(sn)) && r.blast.has(String(tn)));
          return attrs;
        }
        if (focus) {
          const [s, t] = r.graph.extremities(edge);
          if (s === focus || t === focus) {
            attrs.size = (data.size as number) * 2.2;
            attrs.color = r.colors.ink2;
            attrs.zIndex = 2;
          } else {
            attrs.hidden = true;
          }
        }
        return attrs;
      },
    });

    R.current = { graph: g, sigma, layout: null, selected: null, hover: null, blast: null, cycles: null, changes: null, processChain: null, colors };
    // 调试钩子：布局诊断用（读节点坐标判断 FA2 是否真的在动）
    (window as unknown as Record<string, unknown>).__kg = { sigma, getGraph: () => R.current?.graph ?? null, getLayout: () => R.current?.layout ?? null };

    sigma.on("enterNode", ({ node }) => {
      const r = R.current;
      if (!r) return;
      r.hover = node;
      sigma.refresh();
      sigma.getContainer().style.cursor = "pointer";
    });
    sigma.on("leaveNode", () => {
      const r = R.current;
      if (!r) return;
      r.hover = null;
      sigma.refresh();
      sigma.getContainer().style.cursor = "default";
    });
    sigma.on("clickNode", ({ node }) => {
      const r = R.current;
      if (!r) return;
      selectNode(node, false);
    });
    sigma.on("clickStage", () => {
      const r = R.current;
      if (!r) return;
      r.selected = null;
      r.blast = null;
      setBlastOn(false);
      setSelected(null);
      sigma.refresh();
    });

    return () => {
      R.current?.layout?.stop();
      R.current?.layout?.kill();
      sigma.kill();
      R.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasReady]);

  const selectNode = useCallback(
    (id: string, animate = true) => {
      const r = R.current;
      if (!r) return;
      r.selected = id;
      r.blast = null;
      setBlastOn(false);
      setSelected((r.graph.getNodeAttribute(id, "node") as GraphNode) ?? null);
      if (animate && r.sigma.getNodeDisplayData(id)) {
        const p = r.sigma.getNodeDisplayData(id)!;
        r.sigma.getCamera().animate({ x: p.x, y: p.y, ratio: 0.35 }, { duration: 400 });
      }
      r.sigma.refresh();
    },
    []
  );

  // 数据注入 / 变更：重建 graphology 并按模式跑布局
  useEffect(() => {
    const r = R.current;
    if (!r || !clean) return;
    r.layout?.stop();
    r.layout?.kill();
    r.layout = null;
    r.selected = null;
    r.hover = null;
    r.blast = null;
    r.cycles = null;
    r.changes = null;
    r.processChain = null;
    r.graph.clear();
    const g = buildGraph(clean);
    r.graph.import(g.export());
    // export/import 丢了对象引用，把 node 属性补回去
    r.graph.forEachNode((id, attrs) => {
      const n = clean.nodes.find((x) => x.id === id);
      if (n) r!.graph.setNodeAttribute(id, "node", n);
      void attrs;
    });
    setSelected(null);
    setBlastOn(false);
    setCycleOn(false);
    setChangesOn(false);

    if (layoutMode === "force") {
      setTick(1);
      const settings = fa2Settings(r.graph.order);
      const layout = new FA2Layout(r.graph, { settings: { ...settings, outboundAttractionDistribution: true, adjustSizes: true, linLogMode: false } });
      r.layout = layout;
      layout.start();
      window.setTimeout(() => {
        if (R.current?.layout !== layout) return;
        layout.stop();
        layout.kill();
        if (R.current) R.current.layout = null;
        try {
          // 动态边距：FA2 输出坐标跨度数千，静态 margin 无意义，按包围盒比例取
          let span = 1;
          r.graph.forEachNode((_id, a) => {
            span = Math.max(span, Math.abs(a.x), Math.abs(a.y));
          });
          noverlap.assign(r.graph, { maxIterations: 150, settings: { margin: span / 25 } });
        } catch {
          /* noverlap 失败不影响展示 */
        }
        r.sigma.refresh();
        setTick(0);
      }, FA2_DURATION(r.graph.order));
    } else {
      runCircles(r.graph, r.sigma);
    }
    r.sigma.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clean, layoutMode, buildGraph, canvasReady]);

  const runCircles = (g: Graph, sigma: Sigma) => {
    // 同心圆：类别分环，环内按名称排序均匀铺角
    g.forEachNode((id, attrs) => {
      const n = attrs.node as GraphNode | undefined;
      const ring = CATEGORIES.indexOf(n?.category ?? "函数") + 1;
      const peers = g
        .nodes()
        .map((x) => ({ x, a: g.getNodeAttribute(x, "node") as GraphNode | undefined }))
        .filter((p) => CATEGORIES.indexOf(p.a?.category ?? "函数") + 1 === ring);
      const idx = peers.findIndex((p) => p.x === id);
      const angle = (idx / Math.max(peers.length, 1)) * 2 * Math.PI;
      const radius = ring * 55;
      g.setNodeAttribute(id, "x", radius * Math.cos(angle));
      g.setNodeAttribute(id, "y", radius * Math.sin(angle));
    });
    sigma.refresh();
  };

  // 图例开关 → hidden 属性
  useEffect(() => {
    const r = R.current;
    if (!r) return;
    r.graph.forEachNode((id) => {
      const n = r!.graph.getNodeAttribute(id, "node") as GraphNode | undefined;
      r!.graph.setNodeAttribute(id, "hidden", !!(n && hiddenCats[n.category]));
    });
    r.sigma.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hiddenCats, canvasReady]);

  // 影响半径结果 → 红色高亮集合（按节点名匹配）
  useEffect(() => {
    const r = R.current;
    if (!r) return;
    r.blast = blastOn && blast.data ? new Set(blast.data.affected.map((a) => a.name)) : null;
    r.sigma.refresh();
  }, [blast.data, blastOn]);

  // 循环依赖成员 → 红色高亮
  useEffect(() => {
    const r = R.current;
    if (!r) return;
    r.cycles = cycleOn ? new Set((cyclesQ.data?.cycles ?? []).flatMap((c) => c.members)) : null;
    r.sigma.refresh();
  }, [cyclesQ.data, cycleOn]);

  // 未提交变更波及函数 → 橙色高亮
  useEffect(() => {
    const r = R.current;
    if (!r) return;
    r.changes = changesOn && changesQ.data ? new Set(changesQ.data.functions.map((f) => f.name)) : null;
    r.sigma.refresh();
  }, [changesQ.data, changesOn]);

  // 主题切换 → 更新画布配色
  useEffect(() => {
    const r = R.current;
    if (!r) return;
    r.colors = themeColors();
    r.sigma.setSetting("labelColor", { color: r.colors.ink });
    r.sigma.setSetting("defaultEdgeColor", r.colors.line + "80");
    r.sigma.refresh();
  }, [themeMode, themeColors]);

  const camera = {
    in: () => R.current?.sigma.getCamera().animatedZoom({ duration: 250 }),
    out: () => R.current?.sigma.getCamera().animatedUnzoom({ duration: 250 }),
    reset: () => R.current?.sigma.getCamera().animatedReset({ duration: 350 }),
  };

  const searchOptions = useMemo(
    () =>
      (clean?.nodes ?? [])
        .filter((n) => !hiddenCats[n.category] && (showIsolates || !isolatesRef.current.has(n.name)) && n.name.toLowerCase().includes(searching.toLowerCase()))
        .slice(0, 12)
        .map((n) => ({ value: n.id, label: `${n.name}（${n.category}）` })),
    [clean, searching, hiddenCats]
  );

  return (
    <div>
      {!embedded && <PageHeader title="知识图谱" subtitle="接入仓库的代码调用图（Sigma.js WebGL 力导）：默认函数调用层，点图例叠加模块 / 需求 / 用例 / 缺陷溯源" />}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap style={{ display: "flex", justifyContent: "space-between" }}>
          <Space wrap>
            <Select
              style={{ width: 240 }}
              placeholder="选择仓库"
              value={repoId}
              onChange={(v) => {
                setRepoId(v);
                setModule("");
              }}
              options={(repos.data ?? []).map((r) => ({ value: r.id, label: repoName(r.url) }))}
            />
            <Select
              style={{ width: 260 }}
              placeholder="全部模块"
              value={module || undefined}
              allowClear
              onChange={(v) => setModule(v ?? "")}
              options={(graph.data?.modules ?? []).map((m) => ({ value: m, label: m }))}
            />
          </Space>
          {graph.data && (
            <Space wrap size={6}>
              <Tooltip title="无任何调用关系的函数——默认隐藏，点击显示">
                <Tag
                  color={showIsolates ? "cyan" : "default"}
                  style={{ cursor: "pointer", marginInlineEnd: 0 }}
                  onClick={() => setShowIsolates((v) => !v)}
                >
                  孤立 {clean?.isolates.size ?? 0}
                </Tag>
              </Tooltip>
              {cyclesQ.data && cyclesQ.data.total > 0 && (
                <Tooltip title="调用环（循环依赖）——点击在图上高亮成环节点">
                  <Tag color={cycleOn ? "red" : "default"} style={{ cursor: "pointer", marginInlineEnd: 0 }} onClick={() => setCycleOn((v) => !v)}>
                    循环依赖 {cyclesQ.data.total}
                  </Tag>
                </Tooltip>
              )}
              <Tooltip title="执行流（GitNexus Processes）：入口→传递→出口的业务旅程，点击查看列表并在图上高亮">
                <Tag
                  color={processOn ? "purple" : "default"}
                  style={{ cursor: "pointer", marginInlineEnd: 0 }}
                  onClick={() => {
                    setProcessOpen(true);
                    if (!processOn) highlightProcess(null);
                  }}
                >
                  执行流
                </Tag>
              </Tooltip>
              <Tooltip title="变更检测（GitNexus detect_changes）：git 工作区改动 → 受影响函数，点击橙色高亮">
                <Tag
                  color={changesOn ? "orange" : "default"}
                  style={{ cursor: "pointer", marginInlineEnd: 0 }}
                  onClick={async () => {
                    if (!changesOn) {
                      const d = await changesQ.refetch();
                      if (!d.data?.affected_functions) {
                        message.info("git 工作区没有未提交的代码改动");
                        return;
                      }
                    }
                    setChangesOn((v) => !v);
                  }}
                >
                  变更检测{changesQ.data?.affected_functions ? ` · ${changesQ.data.affected_functions} 函数` : ""}
                </Tag>
              </Tooltip>
              <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>
                {graph.data.meta?.head_rev && `HEAD ${graph.data.meta.head_rev} · `}索引更新 {relTime(graph.data.meta?.last_pull)}
                {Object.entries(graph.data.stats)
                  .map(([k, v]) => ` · ${k} ${v}`)
                  .join("")}
              </span>
            </Space>
          )}
        </Space>
      </Card>
      <Card styles={{ body: { padding: 0, position: "relative" } }}>
        {graph.data ? (
          <div style={{ position: "relative" }}>
            <div ref={initRef} style={{ width: "100%", height: "calc(100vh - 236px)", minHeight: 520, background: "var(--tf-bg)", borderRadius: 8 }} />

            {/* 悬浮工具条（左上）：搜索 / 缩放 / 布局 / 影响半径 */}
            <div
              style={{
                position: "absolute",
                top: 10,
                left: 10,
                display: "flex",
                gap: 6,
                alignItems: "center",
                flexWrap: "wrap",
                maxWidth: "calc(100% - 160px)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  gap: 4,
                  alignItems: "center",
                  background: "var(--tf-panel)",
                  border: "1px solid var(--tf-line-strong)",
                  borderRadius: 8,
                  padding: "4px 6px",
                  boxShadow: "var(--tf-card-shadow)",
                }}
              >
                <AutoComplete
                  style={{ width: 200 }}
                  value={searching}
                  options={searchOptions}
                  onSearch={setSearching}
                  onSelect={(id: string) => {
                    selectNode(id);
                    setSearching("");
                  }}
                  placeholder="搜索符号定位"
                  allowClear
                  size="small"
                  variant="borderless"
                />
                <Divider type="vertical" style={{ marginInline: 2 }} />
                <Tooltip title="放大">
                  <Button size="small" type="text" icon={<PlusOutlined />} onClick={camera.in} />
                </Tooltip>
                <Tooltip title="缩小">
                  <Button size="small" type="text" icon={<MinusOutlined />} onClick={camera.out} />
                </Tooltip>
                <Tooltip title="复位视角">
                  <Button size="small" type="text" icon={<RestOutlined />} onClick={camera.reset} />
                </Tooltip>
                <Tooltip title={layoutMode === "force" ? "切换同心圆布局" : "切换力导布局（重新收敛）"}>
                  <Button
                    size="small"
                    type="text"
                    icon={layoutMode === "force" ? <ScissorOutlined /> : <DragOutlined />}
                    onClick={() => setLayoutMode(layoutMode === "force" ? "circles" : "force")}
                  />
                </Tooltip>
                <Tooltip title="重新收敛力导布局">
                  <Button
                    size="small"
                    type="text"
                    icon={<RedoOutlined />}
                    disabled={layoutMode !== "force" || tick === 1}
                    onClick={() => {
                      // 触发重跑：先切圆再切回力导，复用数据注入效应
                      setLayoutMode("circles");
                      window.setTimeout(() => setLayoutMode("force"), 30);
                    }}
                  />
                </Tooltip>
                {selected?.category === "函数" && (
                  <Tooltip title="影响半径：红色为受该函数变更影响的调用方（在线反向 BFS）">
                    <Button
                      size="small"
                      danger={blastOn}
                      type={blastOn ? "primary" : "text"}
                      icon={<AimOutlined />}
                      loading={blast.isFetching}
                      onClick={() => setBlastOn((v) => !v)}
                    />
                  </Tooltip>
                )}
              </div>
              {tick === 1 && (
                <span
                  style={{
                    fontSize: 12,
                    color: "var(--tf-ink-3)",
                    background: "var(--tf-panel)",
                    border: "1px solid var(--tf-line-strong)",
                    borderRadius: 999,
                    padding: "3px 10px",
                  }}
                >
                  力导收敛中…
                </span>
              )}
            </div>

            {/* 关系说明（右上） */}
            <div style={{ position: "absolute", top: 10, right: 10 }}>
              <Popover
                title="关系类型"
                content={
                  <div style={{ fontSize: 12.5, maxWidth: 320 }}>
                    {[
                      ["包含", "仓库 → 模块 → 函数（索引结构）"],
                      ["调用", "函数 → 函数（tree-sitter 调用图）"],
                      ["派生", "需求 → 用例（source_req 溯源）"],
                      ["覆盖", "用例 → 被测函数（target_function）"],
                      ["暴露", "用例 → 缺陷（沙箱失败自动关联）"],
                    ].map(([r, d]) => (
                      <div key={r} style={{ display: "flex", gap: 8, padding: "2px 0" }}>
                        <b style={{ flexShrink: 0 }}>{r}</b>
                        <span style={{ color: "var(--tf-ink-2)" }}>{d}</span>
                      </div>
                    ))}
                  </div>
                }
              >
                <Button size="small" type="text" icon={<QuestionCircleOutlined />} />
              </Popover>
            </div>

            {/* 类别图例（左下，可点击开关类别） */}
            <div
              style={{
                position: "absolute",
                bottom: 10,
                left: 10,
                display: "flex",
                gap: 6,
                flexWrap: "wrap",
                alignItems: "center",
                background: "var(--tf-panel)",
                border: "1px solid var(--tf-line-strong)",
                borderRadius: 8,
                padding: "5px 10px",
                boxShadow: "var(--tf-card-shadow)",
              }}
            >
              {CATEGORIES.map((c, i) => (
                <Tag.CheckableTag
                  key={c}
                  checked={!hiddenCats[c]}
                  onChange={() => setHiddenCats((s) => ({ ...s, [c]: !s[c] }))}
                  style={{ border: "1px solid var(--tf-line-strong)", borderRadius: 999, padding: "0 8px", fontSize: 12, marginInlineEnd: 0 }}
                >
                  <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 999, background: COLORS[i], marginInlineEnd: 5 }} />
                  {c}
                </Tag.CheckableTag>
              ))}
            </div>

            {/* 操作提示（右下） */}
            <span
              style={{
                position: "absolute",
                bottom: 12,
                right: 12,
                fontSize: 12,
                color: "var(--tf-ink-3)",
                background: "var(--tf-panel)",
                border: "1px solid var(--tf-line-strong)",
                borderRadius: 999,
                padding: "3px 10px",
              }}
            >
              滚轮缩放 · 拖拽平移 · 悬停高亮邻接 · 点节点看详情
            </span>
          </div>
        ) : (
          <Empty description="选择仓库后生成图谱" style={{ margin: "120px 0" }} />
        )}
      </Card>
      <Drawer
        title="执行流（入口 → 传递 → 出口）"
        open={processOpen}
        onClose={() => setProcessOpen(false)}
        width={520}
        extra={
          processOn && (
            <Button size="small" onClick={() => highlightProcess(null)}>
              清除高亮
            </Button>
          )
        }
      >
        {processesQ.isLoading && <span style={{ fontSize: 13, color: "var(--tf-ink-3)" }}>分析调用图中…</span>}
        {processesQ.data && processesQ.data.total === 0 && (
          <Empty description="未识别到执行流——需要至少 3 跳的入口调用链" style={{ margin: "32px 0" }} />
        )}
        {processesQ.data && processesQ.data.total > 0 && (
          <div style={{ display: "grid", gap: 12 }}>
            {processesQ.data.processes.map((p, i) => {
              const active = activeProcess?.chain === p.chain;
              return (
                <Card
                  key={i}
                  size="small"
                  style={{ borderColor: active ? "#7c3aed" : undefined }}
                  title={
                    <span style={{ fontSize: 13 }}>
                      <span style={{ color: "#7c3aed", fontFamily: "Consolas, monospace", marginInlineEnd: 6 }}>{p.depth} 跳</span>
                      {p.entry}
                    </span>
                  }
                  extra={
                    <Button size="small" type={active ? "primary" : "default"} ghost={active} onClick={() => highlightProcess(active ? null : p.chain)}>
                      {active ? "取消高亮" : "图上高亮"}
                    </Button>
                  }
                >
                  <div style={{ fontSize: 12.5, fontFamily: "Consolas, monospace", lineHeight: 1.8, wordBreak: "break-all" }}>
                    {p.chain.map((n, j) => (
                      <span key={j}>
                        {j > 0 && <span style={{ color: "#7c3aed", marginInline: 4 }}>→</span>}
                        <span style={{ color: p.exits.includes(n) ? "#b45309" : undefined }}>{n}</span>
                      </span>
                    ))}
                  </div>
                  <div style={{ marginBlockStart: 6, fontSize: 12, color: "var(--tf-ink-3)" }}>
                    出口：{p.exits.length ? p.exits.join("、") : "未识别（纯内存链路）"}
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </Drawer>
      <Drawer
        title={selected ? `${selected.category} · ${selected.name}` : ""}
        open={!!selected}
        onClose={() => setSelected(null)}
        width={480}
      >
        {selected && (
          <>
            <Space wrap style={{ marginBottom: 12 }}>
              <Tag>{selected.category}</Tag>
              {typeof selected.度数 === "number" && <Tag>度数 {selected.度数}</Tag>}
              {selected.module && <Tag>{selected.module}</Tag>}
            </Space>
            {/* 引用导航（GitNexus context 轻量版）：直接调用方/被调，点击跳转聚焦 */}
            {(() => {
              const callers = (clean?.allEdges ?? []).filter((e) => e.relation !== "包含" && e.target === selected.id).map((e) => e.source);
              const callees = (clean?.allEdges ?? []).filter((e) => e.relation !== "包含" && e.source === selected.id).map((e) => e.target);
              if (!callers.length && !callees.length) return null;
              const chip = (ids: string[]) => (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
                  {ids.map((id) => (
                    <Tag key={id} style={{ cursor: "pointer" }} onClick={() => selectNode(id)}>
                      {nodeById.get(id)?.name ?? id}
                    </Tag>
                  ))}
                </div>
              );
              return (
                <div style={{ marginBottom: 12, display: "grid", gap: 10 }}>
                  {callers.length > 0 && (
                    <div>
                      <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>被谁调用（{callers.length}）</span>
                      {chip(callers)}
                    </div>
                  )}
                  {callees.length > 0 && (
                    <div>
                      <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>调用谁（{callees.length}）</span>
                      {chip(callees)}
                    </div>
                  )}
                </div>
              );
            })()}
            <Table
              rowKey="k"
              size="small"
              pagination={false}
              dataSource={Object.entries(selected)
                .filter(([k]) => k !== "id" && k !== "node")
                .map(([k, v]) => ({ k, v: typeof v === "object" ? JSON.stringify(v) : String(v ?? "-") }))}
              columns={[
                { title: "属性", dataIndex: "k", width: 120 },
                { title: "值", dataIndex: "v", ellipsis: true },
              ]}
            />
            {selected.category === "函数" && (
              <>
                <Button
                  block
                  style={{ marginTop: 12 }}
                  danger={blastOn}
                  type={blastOn ? "primary" : "default"}
                  icon={<AimOutlined />}
                  loading={blast.isFetching}
                  onClick={() => setBlastOn((v) => !v)}
                >
                  {blastOn ? "关闭影响半径" : "影响半径（受该函数变更影响的调用方）"}
                </Button>
                {blast.data?.certainty && (
                  <Tooltip title={blast.data.basis}>
                    <div style={{ marginBlockStart: 8, fontSize: 12, color: "var(--tf-ink-3)", cursor: "help" }}>
                      <Tag color="orange" style={{ marginInlineEnd: 4 }}>静态下界</Tag>
                      基于静态调用图；动态分发/反射调用不可见，运行时真实影响可能更大
                    </div>
                  </Tooltip>
                )}
              </>
            )}
          </>
        )}
      </Drawer>
    </div>
  );
}
