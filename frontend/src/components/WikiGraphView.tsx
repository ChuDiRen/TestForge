/** Wiki 互链图谱（OpenWiki WikiGraphView 移植，MIT）：
 *  d3-force + Canvas 2D 事件驱动绘制（无 rAF 空转，收敛后零开销）。
 *  节点 = Wiki 页（level 着色，stale 半透明），边 = TF-IDF 余弦互链（/api/wiki/graph）。
 *  交互：拖节点 / 平移 / 滚轮缩放 / 悬停显邻接 / 点击开详情。 */
import { Component, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { forceCenter, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import { useThemeMode } from "../hooks";

export interface WikiGraphNode {
  id: string;
  title: string;
  level: string; // repo | module | function
  module: string;
  stale: boolean;
}
export interface WikiGraphEdge {
  source: string;
  target: string;
  weight: number;
}
export interface WikiGraphData {
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
}

// 节点色对齐知识图谱页类别色板：仓库墨色 / 模块杉青 / 函数深杉青
const LEVEL_COLORS: Record<string, string> = {
  repo: "#24272b",
  module: "#0d7d72",
  function: "#0891b2",
};
const LEVEL_LABELS: Record<string, string> = { repo: "仓库", module: "模块", function: "函数" };

interface GNode extends SimulationNodeDatum {
  id: string;
  title: string;
  short: string;
  level: string;
  stale: boolean;
  degree: number;
}
interface GLink extends SimulationLinkDatum<GNode> {
  weight: number;
}

/** Canvas 渲染异常不拖垮整个 Wiki 页（对齐 OpenWiki GraphErrorBoundary） */
class GraphErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: Error) {
    return { error: e.message };
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ textAlign: "center", padding: "64px 0" }}>
          <p style={{ fontSize: 14, fontWeight: 600 }}>互链图谱渲染出错</p>
          <p style={{ fontSize: 12, color: "var(--tf-ink-3)", marginTop: 4 }}>{this.state.error}</p>
          <button
            onClick={() => this.setState({ error: null })}
            style={{ marginTop: 12, padding: "6px 14px", borderRadius: 8, fontSize: 12, cursor: "pointer", border: "1px solid var(--tf-line-strong)", background: "var(--tf-panel)" }}
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function hexToRgba(hex: string, alpha: number): string {
  const m = hex.replace("#", "");
  const v = m.length === 3 ? m.split("").map((x) => x + x).join("") : m.slice(0, 6);
  return `rgba(${parseInt(v.slice(0, 2), 16)},${parseInt(v.slice(2, 4), 16)},${parseInt(v.slice(4, 6), 16)},${alpha})`;
}

function WikiGraphViewInner({
  data,
  loading,
  active,
  onSelectPage,
}: {
  data: WikiGraphData | undefined;
  loading: boolean;
  active: boolean;
  onSelectPage: (id: string) => void;
}) {
  const mode = useThemeMode();
  const isDark = mode === "dark";
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const simRef = useRef<ReturnType<typeof forceSimulation<GNode, GLink>> | null>(null);
  const nodesRef = useRef<GNode[]>([]);
  const linksRef = useRef<GLink[]>([]);
  const neighborsRef = useRef<Map<string, Set<string>>>(new Map());
  const camRef = useRef({ x: 0, y: 0, zoom: 1 });
  const dragRef = useRef<{ node: GNode | null; startX: number; startY: number; moved: boolean }>({ node: null, startX: 0, startY: 0, moved: false });
  const panRef = useRef<{ active: boolean; lastX: number; lastY: number }>({ active: false, lastX: 0, lastY: 0 });
  const hoverRef = useRef<GNode | null>(null);
  const activeRef = useRef(active);
  const isDarkRef = useRef(isDark);
  // 点击回调走 ref，避免 Pointer 事件 effect 因回调身份变化反复解绑
  const onSelectRef = useRef(onSelectPage);
  onSelectRef.current = onSelectPage;

  useEffect(() => {
    activeRef.current = active;
    const simulation = simRef.current;
    if (!active) {
      simulation?.stop();
      return;
    }
    draw();
    if (simulation && simulation.alpha() > simulation.alphaMin()) simulation.restart();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const getNodeRadius = useCallback((node: GNode) => Math.max(4, 3 + Math.sqrt(node.degree || 0) * 2), []);

  const screenToGraph = useCallback((sx: number, sy: number, canvas: HTMLCanvasElement) => {
    const rect = canvas.getBoundingClientRect();
    const cam = camRef.current;
    return {
      x: (sx - rect.left - rect.width / 2) / cam.zoom - cam.x,
      y: (sy - rect.top - rect.height / 2) / cam.zoom - cam.y,
    };
  }, []);

  const findNode = useCallback((gx: number, gy: number) => {
    const nodes = nodesRef.current;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const r = getNodeRadius(n) + 4;
      const dx = (n.x || 0) - gx;
      const dy = (n.y || 0) - gy;
      if (dx * dx + dy * dy < r * r) return n;
    }
    return null;
  }, [getNodeRadius]);

  const draw = useCallback(() => {
    if (!activeRef.current) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    const cam = camRef.current;
    const dark = isDarkRef.current;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = dark ? "#1d2023" : "#f7f7f5";
    ctx.fillRect(0, 0, w, h);

    ctx.save();
    ctx.translate(w / 2, h / 2);
    ctx.scale(cam.zoom, cam.zoom);
    ctx.translate(cam.x, cam.y);

    // 悬停邻接集：非邻居压暗，看得出这个页面连着谁（对齐知识图谱页悬停语义）
    const hovered = hoverRef.current;
    const neighbors = hovered ? neighborsRef.current.get(hovered.id) : undefined;

    // Links：hover 时只亮邻接边
    ctx.strokeStyle = dark ? "rgba(168,162,158,0.22)" : "rgba(120,113,108,0.26)";
    ctx.lineWidth = 0.5 / cam.zoom;
    for (const link of linksRef.current) {
      const s = link.source as GNode;
      const t = link.target as GNode;
      if (s.x == null || t.x == null) continue;
      if (hovered && neighbors && !(neighbors.has(s.id) && neighbors.has(t.id))) continue;
      ctx.beginPath();
      ctx.moveTo(s.x, s.y!);
      ctx.lineTo(t.x, t.y!);
      ctx.stroke();
    }

    // Nodes
    for (const node of nodesRef.current) {
      if (node.x == null) continue;
      if (hovered && hovered.id !== node.id && !(neighbors && neighbors.has(node.id))) {
        ctx.globalAlpha = 0.18;
      } else {
        ctx.globalAlpha = node.stale ? 0.45 : 1;
      }
      const r = getNodeRadius(node);
      ctx.beginPath();
      ctx.arc(node.x, node.y!, r, 0, Math.PI * 2);
      ctx.fillStyle = LEVEL_COLORS[node.level] || "#A8A29E";
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    // 标签：放大到 1.5 倍以上才画，控制密度
    if (cam.zoom > 1.5) {
      const fontSize = Math.min(10 / cam.zoom, 10);
      ctx.font = `${fontSize}px Consolas, DengXian, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = dark ? "rgba(250,250,248,0.7)" : "rgba(28,25,23,0.7)";
      for (const node of nodesRef.current) {
        if (node.x == null) continue;
        if (hovered && hovered.id !== node.id && !(neighbors && neighbors.has(node.id))) continue;
        const r = getNodeRadius(node);
        ctx.fillText(node.short, node.x, node.y! + r + 2);
      }
    }

    // Hover 描边 + 全标题
    if (hovered && hovered.x != null) {
      const r = getNodeRadius(hovered);
      ctx.beginPath();
      ctx.arc(hovered.x, hovered.y!, r + 2, 0, Math.PI * 2);
      ctx.strokeStyle = "#0891b2";
      ctx.lineWidth = 2 / cam.zoom;
      ctx.stroke();
      ctx.font = `bold ${12 / cam.zoom}px Consolas, DengXian, monospace`;
      ctx.fillStyle = dark ? "#FAFAF8" : "#1C1917";
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.fillText(hovered.title, hovered.x, hovered.y! - r - 4);
    }

    ctx.restore();
  }, [getNodeRadius]);

  useEffect(() => {
    isDarkRef.current = isDark;
    draw();
  }, [draw, isDark]);

  // 数据 → 仿真（悬空边过滤 + 度数统计 + 邻接表 + 中文标签截断）
  useEffect(() => {
    if (!data || data.nodes.length === 0) return;
    const nodes: GNode[] = data.nodes.map((n) => ({
      id: n.id,
      title: n.title,
      short: n.title.replace(/^(函数|模块|仓库)\s*/, "").slice(0, 12),
      level: n.level,
      stale: n.stale,
      degree: 0,
    }));
    const nodeIds = new Set(nodes.map((n) => n.id));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const neighbors = new Map<string, Set<string>>();
    const links: GLink[] = data.edges
      .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target)
      .map((e) => {
        byId.get(e.source)!.degree += 1;
        byId.get(e.target)!.degree += 1;
        if (!neighbors.has(e.source)) neighbors.set(e.source, new Set());
        if (!neighbors.has(e.target)) neighbors.set(e.target, new Set());
        neighbors.get(e.source)!.add(e.target);
        neighbors.get(e.target)!.add(e.source);
        return { source: e.source, target: e.target, weight: e.weight };
      });
    nodesRef.current = nodes;
    linksRef.current = links;
    neighborsRef.current = neighbors;

    simRef.current?.stop();
    const sim = forceSimulation<GNode, GLink>(nodes)
      .force("charge", forceManyBody<GNode>().strength(-80).distanceMax(300))
      .force("link", forceLink<GNode, GLink>(links).id((d) => d.id).distance(50))
      .force("center", forceCenter(0, 0).strength(0.05))
      .force("x", forceX<GNode>(0).strength(0.02))
      .force("y", forceY<GNode>(0).strength(0.02))
      .alphaDecay(0.028)
      .velocityDecay(0.4)
      .alpha(1)
      .on("tick", () => draw())
      .on("end", () => draw());
    simRef.current = sim;
    draw();
    if (!activeRef.current) sim.stop();
    return () => {
      sim.stop();
    };
  }, [data, draw]);

  // Canvas 尺寸自适应（devicePixelRatio）
  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const resize = () => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      draw();
    };
    resize();
    const obs = new ResizeObserver(resize);
    obs.observe(container);
    return () => obs.disconnect();
  }, [draw]);

  // Pointer 交互：拖节点（fx/fy 固定）/ 平移 / 悬停 / 点击（无位移才选中）
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const onDown = (e: PointerEvent) => {
      const g = screenToGraph(e.clientX, e.clientY, canvas);
      const node = findNode(g.x, g.y);
      if (node) {
        dragRef.current = { node, startX: e.clientX, startY: e.clientY, moved: false };
        node.fx = node.x;
        node.fy = node.y;
        simRef.current?.alphaTarget(0.3).restart();
        canvas.setPointerCapture(e.pointerId);
      } else {
        panRef.current = { active: true, lastX: e.clientX, lastY: e.clientY };
      }
    };

    const onMove = (e: PointerEvent) => {
      const drag = dragRef.current;
      if (drag.node) {
        if (Math.abs(e.clientX - drag.startX) > 3 || Math.abs(e.clientY - drag.startY) > 3) drag.moved = true;
        const g = screenToGraph(e.clientX, e.clientY, canvas);
        drag.node.fx = g.x;
        drag.node.fy = g.y;
      } else if (panRef.current.active) {
        const cam = camRef.current;
        cam.x += (e.clientX - panRef.current.lastX) / cam.zoom;
        cam.y += (e.clientY - panRef.current.lastY) / cam.zoom;
        panRef.current.lastX = e.clientX;
        panRef.current.lastY = e.clientY;
        draw();
      } else {
        const g = screenToGraph(e.clientX, e.clientY, canvas);
        hoverRef.current = findNode(g.x, g.y);
        canvas.style.cursor = hoverRef.current ? "pointer" : "grab";
        draw();
      }
    };

    const onUp = (e: PointerEvent) => {
      const drag = dragRef.current;
      if (drag.node) {
        if (!drag.moved) onSelectRef.current(drag.node.id);
        drag.node.fx = null;
        drag.node.fy = null;
        simRef.current?.alphaTarget(0);
        drag.node = null;
        canvas.releasePointerCapture(e.pointerId);
      }
      panRef.current.active = false;
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      camRef.current.zoom = Math.max(0.1, Math.min(10, camRef.current.zoom * (e.deltaY > 0 ? 0.97 : 1.03)));
      draw();
    };

    const onLeave = () => {
      hoverRef.current = null;
      draw();
    };

    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, [screenToGraph, findNode, draw]);

  const levelsPresent = Array.from(new Set((data?.nodes ?? []).map((n) => n.level)));

  return (
    <div
      ref={containerRef}
      style={{
        position: "relative",
        height: "calc(100vh - 300px)",
        minHeight: 480,
        borderRadius: 8,
        overflow: "hidden",
        backgroundColor: isDark ? "#1d2023" : "#f7f7f5",
        border: "1px solid var(--tf-line-strong)",
      }}
    >
      {/* 图例（左上） */}
      <div style={{ position: "absolute", top: 10, left: 10, zIndex: 10, display: "flex", gap: 12, pointerEvents: "none" }}>
        {levelsPresent.map((lv) => (
          <span key={lv} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--tf-ink-2)" }}>
            <span style={{ width: 8, height: 8, borderRadius: 999, background: LEVEL_COLORS[lv] ?? "#A8A29E", display: "inline-block" }} />
            {LEVEL_LABELS[lv] ?? lv}
          </span>
        ))}
        {(data?.nodes ?? []).some((n) => n.stale) && (
          <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>· 半透明 = stale</span>
        )}
      </div>

      {/* 统计（右上） */}
      <div
        style={{
          position: "absolute",
          top: 10,
          right: 10,
          zIndex: 10,
          fontSize: 12,
          color: "var(--tf-ink-3)",
          background: "var(--tf-panel)",
          border: "1px solid var(--tf-line-strong)",
          borderRadius: 999,
          padding: "3px 10px",
          pointerEvents: "none",
        }}
      >
        {(data?.nodes ?? []).length} 页 · {(data?.edges ?? []).length} 条互链
      </div>

      <canvas ref={canvasRef} style={{ display: "block", width: "100%", height: "100%" }} />

      {loading && (
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
          <span style={{ fontSize: 12, color: "var(--tf-ink-3)", background: "var(--tf-panel)", border: "1px solid var(--tf-line-strong)", borderRadius: 999, padding: "4px 12px" }}>
            互链计算中（TF-IDF）…
          </span>
        </div>
      )}

      {!loading && (data?.nodes.length ?? 0) === 0 && (
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
          <span style={{ fontSize: 13, color: "var(--tf-ink-3)" }}>暂无 Wiki 页面——先重建 Wiki 或选择仓库</span>
        </div>
      )}

      {/* 操作提示（右下） */}
      <span
        style={{
          position: "absolute",
          bottom: 10,
          right: 10,
          fontSize: 12,
          color: "var(--tf-ink-3)",
          background: "var(--tf-panel)",
          border: "1px solid var(--tf-line-strong)",
          borderRadius: 999,
          padding: "3px 10px",
          pointerEvents: "none",
        }}
      >
        滚轮缩放 · 拖拽平移 / 拖节点 · 悬停看邻接 · 点击开页面
      </span>
    </div>
  );
}

export function WikiGraphView(props: Parameters<typeof WikiGraphViewInner>[0]) {
  return (
    <GraphErrorBoundary>
      <WikiGraphViewInner {...props} />
    </GraphErrorBoundary>
  );
}
