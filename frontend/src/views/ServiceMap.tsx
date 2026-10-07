import { PageHeader } from "../components/PageHeader";
import { useMemo, useState } from "react";
import { Button, Card, Col, Empty, message, Row, Table, Tag } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { get, post } from "../api";

interface ContractRow {
  id: number;
  name: string;
  type: string;
  provider_repo: string;
  version: string;
  consumers: string[];
  last_breaking: boolean;
  last_diff: string[];
}

/** 服务调用链路：由契约中心真实数据推导（谁调用谁 · 什么协议 · 哪条 breaking），无写死拓扑。
 *  服务节点 = 消费方 ∪ 提供方，按调用方向做最长路径分层（入口在左，底层服务在右），
 *  多跳依赖自动展开多列；同对多契约走错弧贝塞尔，标签近源错位，任意数量边不叠字。 */
function ServiceMap({ contracts, onImpact }: { contracts: ContractRow[]; onImpact: (c: ContractRow) => void }) {
  const [hover, setHover] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  const services = useMemo(() => {
    const out: string[] = [];
    for (const c of contracts) {
      for (const u of c.consumers || []) if (!out.includes(u)) out.push(u);
      if (c.provider_repo && !out.includes(c.provider_repo)) out.push(c.provider_repo);
    }
    return out;
  }, [contracts]);

  // 调用方向 consumer→provider 的最长路径分层（迭代松弛，环按迭代上限截断）
  const { cols, maxLayer } = useMemo(() => {
    const layerOf: Record<string, number> = {};
    services.forEach((s) => (layerOf[s] = 0));
    for (let it = 0; it <= services.length; it++) {
      let changed = false;
      for (const c of contracts)
        for (const u of c.consumers || []) {
          const next = (layerOf[u] ?? 0) + 1;
          if (next > (layerOf[c.provider_repo] ?? 0)) {
            layerOf[c.provider_repo] = next;
            changed = true;
          }
        }
      if (!changed) break;
    }
    const max = Math.max(0, ...services.map((s) => layerOf[s] ?? 0));
    const columns: string[][] = Array.from({ length: max + 1 }, () => []);
    for (const s of services) columns[layerOf[s] ?? 0].push(s);
    columns.forEach((col) => col.sort());
    return { cols: columns, maxLayer: max };
  }, [services, contracts]);

  // 每个服务的调用统计与 breaking 标记
  const stats = useMemo(() => {
    const outDeg: Record<string, number> = {};
    const inDeg: Record<string, number> = {};
    const breaking: Record<string, boolean> = {};
    for (const c of contracts) {
      for (const u of c.consumers || []) {
        outDeg[u] = (outDeg[u] ?? 0) + 1;
        if (c.last_breaking) breaking[u] = true;
      }
      inDeg[c.provider_repo] = (inDeg[c.provider_repo] ?? 0) + 1;
      if (c.last_breaking) breaking[c.provider_repo] = true;
    }
    return { outDeg, inDeg, breaking };
  }, [contracts]);

  const W = 760;
  const NODE_W = 184;
  const NODE_H = 54;
  const PAD = 30;
  const rows = Math.max(...cols.map((c) => c.length), 1);
  const H = Math.max(300, rows * 118 + PAD);
  const gap = maxLayer > 0 ? (W - PAD * 2 - NODE_W) / maxLayer : 0;
  const posOf = (name: string) => {
    for (let l = 0; l < cols.length; l++) {
      const i = cols[l].indexOf(name);
      if (i >= 0) return { x: PAD + NODE_W / 2 + l * gap, y: PAD + ((i + 0.5) * (H - PAD * 2)) / Math.max(cols[l].length, 1) };
    }
    return null;
  };

  // 三次贝塞尔取点：B(t) = (1-t)³P0 + 3(1-t)²tC1 + 3(1-t)t²C2 + t³P3
  const cubicAt = (p0: number[], c1: number[], c2: number[], p3: number[], t: number) => {
    const u = 1 - t;
    return [
      u * u * u * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * p3[0],
      u * u * u * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * p3[1],
    ];
  };
  // 标签胶囊宽度：ASCII 6.2px、全角/符号 11px（fontSize 11）
  const textW = (s: string) => [...s].reduce((w, ch) => w + (ch.charCodeAt(0) > 0x2e7f ? 11 : 6.2), 0) + 14;

  // 组装边：按（消费方→提供方）对计数，对内第 k 条做弧度错开
  //（本文件导出组件名 Map 遮蔽了全局 Map 构造器，计数用普通对象）
  const pairCount: Record<string, number> = {};
  const pairSeen: Record<string, number> = {};
  for (const c of contracts)
    for (const u of c.consumers || []) {
      const key = `${u}\u2192${c.provider_repo}`;
      pairCount[key] = (pairCount[key] ?? 0) + 1;
    }
  const edges = contracts
    .flatMap((c) =>
      (c.consumers || []).map((u) => {
        const a = posOf(u);
        const b = posOf(c.provider_repo);
        if (!a || !b) return null;
        const key = `${u}\u2192${c.provider_repo}`;
        const k = pairSeen[key] ?? 0;
        pairSeen[key] = k + 1;
        const p0 = [a.x + NODE_W / 2, a.y];
        const p3 = [b.x - NODE_W / 2, b.y];
        const bow = (k - ((pairCount[key] ?? 1) - 1) / 2) * 36;
        const c1 = [p0[0] + (p3[0] - p0[0]) * 0.42, p0[1] + bow];
        const c2 = [p3[0] - (p3[0] - p0[0]) * 0.42, p3[1] + bow];
        const d = `M ${p0[0]} ${p0[1]} C ${c1[0]} ${c1[1]}, ${c2[0]} ${c2[1]}, ${p3[0]} ${p3[1]}`;
        const label = `${c.name}@${c.version}${c.last_breaking ? " ⚠ breaking" : ""}`;
        const t = 0.3 + 0.16 * (k % 3);
        const [lx, ly] = cubicAt(p0, c1, c2, p3, t);
        return { id: `${c.id}-${u}`, c, u, d, label, lx, ly };
      })
    )
    .filter(Boolean) as { id: string; c: ContractRow; u: string; d: string; label: string; lx: number; ly: number }[];

  if (!contracts.length) {
    return <Empty description="暂无契约数据——注册契约后自动生成调用链路" style={{ margin: "32px 0" }} />;
  }

  const edgeActive = (u: string, provider: string) => !focus || focus === u || focus === provider;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMin meet" style={{ width: "100%", height: "auto", background: "var(--tf-bg)", borderRadius: 8 }}>
      <defs>
        <marker id="tf-arrow" markerWidth="8" markerHeight="8" refX="7" refY="2" orient="auto">
          <path d="M0,0 L0,4 L7,2 z" fill="var(--tf-ink-3)" />
        </marker>
        <marker id="tf-arrow-bad" markerWidth="8" markerHeight="8" refX="7" refY="2" orient="auto">
          <path d="M0,0 L0,4 L7,2 z" fill="var(--tf-bad)" />
        </marker>
      </defs>
      {edges.map((e) => {
        const breaking = e.c.last_breaking;
        const hovered = hover === e.id;
        const active = edgeActive(e.u, e.c.provider_repo);
        return (
          <g
            key={e.id}
            onClick={() => onImpact(e.c)}
            style={{ cursor: "pointer", opacity: active ? 1 : 0.12 }}
            onMouseEnter={() => setHover(e.id)}
            onMouseLeave={() => setHover(null)}
          >
            <path d={e.d} fill="none" stroke="transparent" strokeWidth={18} />
            <path
              d={e.d}
              fill="none"
              stroke={breaking ? "var(--tf-bad)" : hovered ? "var(--tf-primary)" : "var(--tf-ink-3)"}
              strokeWidth={breaking ? 2 : hovered ? 2.4 : 1.4}
              strokeDasharray={breaking ? "6 3" : undefined}
              opacity={breaking || hovered ? 1 : 0.75}
              markerEnd={breaking ? "url(#tf-arrow-bad)" : "url(#tf-arrow)"}
            />
            <g transform={`translate(${e.lx}, ${e.ly - 15})`}>
              <rect
                x={-textW(e.label) / 2}
                y={-1}
                width={textW(e.label)}
                height={19}
                rx={9.5}
                fill="var(--tf-panel)"
                stroke={breaking ? "var(--tf-bad)" : "var(--tf-line-strong)"}
                strokeWidth={1}
              />
              <text textAnchor="middle" y={13} fontSize={11} fill={breaking ? "var(--tf-bad)" : "var(--tf-ink-2)"} style={{ fontWeight: breaking ? 600 : 400 }}>
                {e.label}
              </text>
            </g>
          </g>
        );
      })}
      {cols.flatMap((col, layer) =>
        col.map((n, i) => {
          const cy = PAD + ((i + 0.5) * (H - PAD * 2)) / Math.max(col.length, 1);
          const cx = PAD + NODE_W / 2 + layer * gap;
          const parts = [
            stats.outDeg[n] ? `调用 ${stats.outDeg[n]}` : "",
            stats.inDeg[n] ? `被调 ${stats.inDeg[n]}` : "",
          ].filter(Boolean);
          const focused = focus === n;
          return (
            <g
              key={n}
              style={{ cursor: "pointer" }}
              onClick={() => setFocus(focused ? null : n)}
              onMouseEnter={() => setHover(`node:${n}`)}
              onMouseLeave={() => setHover(null)}
            >
              <rect
                className="tf-sm-node"
                x={cx - NODE_W / 2}
                y={cy - NODE_H / 2}
                width={NODE_W}
                height={NODE_H}
                rx={10}
                strokeWidth={focused ? 1.6 : 1}
                style={focused ? { stroke: "var(--tf-primary)" } : undefined}
              />
              <text className="tf-sm-node-name" x={cx} y={cy - 2} textAnchor="middle" fontSize={12.5} fontWeight={600}>
                {n}
              </text>
              <text className="tf-sm-node-sub" x={cx} y={cy + 16} textAnchor="middle" fontSize={10.5}>
                {parts.join(" · ") || "孤立服务"}
              </text>
              {stats.breaking[n] && <circle className="tf-sm-node-breaking" cx={cx + NODE_W / 2 - 12} cy={cy - NODE_H / 2 + 12} r={4} />}
            </g>
          );
        })
      )}
    </svg>
  );
}

export function Map() {
  const contracts = useQuery({ queryKey: ["contracts"], queryFn: () => get<ContractRow[]>("/api/contracts"), refetchInterval: 15000 });
  const [impact, setImpact] = useState<any[] | null>(null);
  const [impactContract, setImpactContract] = useState<ContractRow | null>(null);
  const [loading, setLoading] = useState(false);

  const runImpact = async (c: ContractRow) => {
    setLoading(true);
    setImpactContract(c);
    try {
      const res = await post<any[]>(`/api/contracts/${c.id}/impact`, { to_v: c.version });
      setImpact(res);
    } finally {
      setLoading(false);
    }
  };

  const regen = useMutation({
    mutationFn: (c: ContractRow) =>
      post<{ affected_cases: number; targets: string[]; generations: { generation_id: string }[] }>(
        `/api/contracts/${c.id}/regenerate`,
        { to_v: c.version }
      ),
    onSuccess: (res) => {
      message.success(
        res.targets.length
          ? `已入队完整重生成：${res.targets.join(", ")}（进度看任务队列）`
          : "受影响用例未解析出可生成目标"
      );
    },
    onError: (e: any) => message.error(e.message),
  });

  return (
    <div>
      <PageHeader title="服务地图 / 契约" subtitle="契约推导的服务调用链路 · 点服务高强调用边 · 点 breaking 边看影响分析" />
      <Row gutter={[16, 16]}>
      <Col xs={24} lg={14}>
        <Card
          title="服务调用链路"
          extra={
            <span style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 12, color: "var(--tf-ink-2)" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="var(--tf-ink-3)" strokeWidth="1.5" /></svg>
                调用（契约版本标于边上）
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="var(--tf-bad)" strokeWidth="2" strokeDasharray="5 3" /></svg>
                breaking · 点边看影响分析
              </span>
            </span>
          }
        >
          <ServiceMap contracts={contracts.data ?? []} onImpact={runImpact} />
        </Card>
      </Col>
      <Col xs={24} lg={10}>
        <Card title="契约中心" size="small">
          <Table<ContractRow>
            rowKey="id"
            size="small"
            scroll={{ x: 480 }}
            pagination={false}
            dataSource={contracts.data ?? []}
            columns={[
              { title: "契约", dataIndex: "name" },
              { title: "类型", dataIndex: "type", width: 70 },
              { title: "版本", dataIndex: "version", width: 80 },
              {
                title: "状态",
                dataIndex: "last_breaking",
                width: 100,
                render: (b: boolean) => (b ? <Tag color="red">breaking</Tag> : <Tag color="green">稳定</Tag>),
              },
            ]}
          />
        </Card>
        {impact && (
          <Card
            title="影响分析面板（受影响资产清单）"
            size="small"
            style={{ marginTop: 16 }}
            loading={loading}
            extra={
              impactContract && (
                <Button size="small" type="primary" loading={regen.isPending} onClick={() => regen.mutate(impactContract)}>
                  受影响重生成（完整闭环）
                </Button>
              )
            }
          >
            <Table
              rowKey={(_, i) => String(i)}
              size="small"
              pagination={false}
              dataSource={impact}
              columns={[
                { title: "资产", dataIndex: "asset_id", ellipsis: true },
                { title: "类型", dataIndex: "asset_type", width: 90 },
                { title: "原因", dataIndex: "reason", ellipsis: true },
              ]}
            />
          </Card>
        )}
      </Col>
    </Row>
    </div>
  );
}
