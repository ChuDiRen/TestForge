import { useMemo, useState } from "react";
import { Card, Col, Empty, Row, Table, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
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

/** 服务依赖拓扑：节点与边全部由契约中心真实数据构建（消费方 → 提供方），无写死拓扑 */
function ServiceMap({ contracts, onImpact }: { contracts: ContractRow[]; onImpact: (c: ContractRow) => void }) {
  const consumers = useMemo(() => {
    const out: string[] = [];
    for (const c of contracts) for (const u of c.consumers || []) if (!out.includes(u)) out.push(u);
    return out;
  }, [contracts]);
  const providers = useMemo(() => {
    const out: string[] = [];
    for (const c of contracts) if (c.provider_repo && !out.includes(c.provider_repo)) out.push(c.provider_repo);
    return out;
  }, [contracts]);

  const W = 480;
  const rows = Math.max(consumers.length, providers.length, 1);
  const H = Math.max(200, rows * 80);
  const y = (i: number, n: number) => ((i + 0.5) * H) / Math.max(n, 1);
  const posOf = (name: string, list: string[], x: number) => {
    const i = list.indexOf(name);
    return i >= 0 ? { x, y: y(i, list.length) } : null;
  };

  if (!contracts.length) {
    return <Empty description="暂无契约数据——注册契约后自动生成真实拓扑" style={{ margin: "32px 0" }} />;
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMin meet" style={{ width: "100%", height: "auto", background: "#fafafa", borderRadius: 6 }}>
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="2" orient="auto">
          <path d="M0,0 L0,4 L7,2 z" fill="#666" />
        </marker>
      </defs>
      {contracts.flatMap((c) =>
        (c.consumers || []).map((u) => {
          const a = posOf(u, consumers, 105);
          const b = posOf(c.provider_repo, providers, 375);
          if (!a || !b) return null;
          const breaking = c.last_breaking;
          return (
            <g key={`${c.id}-${u}`} onClick={() => onImpact(c)} style={{ cursor: "pointer" }}>
              <line
                x1={a.x + 55}
                y1={a.y}
                x2={b.x - 55}
                y2={b.y}
                stroke={breaking ? "#f5222d" : "#999"}
                strokeWidth={breaking ? 2.5 : 1.5}
                strokeDasharray={breaking ? "6 3" : undefined}
                markerEnd="url(#arrow)"
              />
              <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6} textAnchor="middle" fontSize={10} fill={breaking ? "#f5222d" : "#555"}>
                {c.name}@{c.version}
                {breaking ? " ⚠breaking" : ""}
              </text>
            </g>
          );
        })
      )}
      {consumers.map((n, i) => (
        <g key={n}>
          <rect x={50} y={y(i, consumers.length) - 20} width={110} height={40} rx={8} fill="#0b1021" opacity={0.85} />
          <text x={105} y={y(i, consumers.length) + 5} textAnchor="middle" fill="#fff" fontSize={12}>
            {n}
          </text>
        </g>
      ))}
      {providers.map((n, i) => (
        <g key={n}>
          <rect x={320} y={y(i, providers.length) - 20} width={110} height={40} rx={8} fill="#4f46e5" opacity={0.92} />
          <text x={375} y={y(i, providers.length) + 5} textAnchor="middle" fill="#fff" fontSize={12}>
            {n}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function Map() {
  const contracts = useQuery({ queryKey: ["contracts"], queryFn: () => get<ContractRow[]>("/api/contracts"), refetchInterval: 15000 });
  const [impact, setImpact] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(false);

  const runImpact = async (c: ContractRow) => {
    setLoading(true);
    try {
      const res = await post<any[]>(`/api/contracts/${c.id}/impact`, { to_v: c.version });
      setImpact(res);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={14}>
        <Card title="服务地图（真实契约拓扑 · 点 breaking 边看影响分析）">
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
          <Card title="影响分析面板（受影响资产清单）" size="small" style={{ marginTop: 16 }} loading={loading}>
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
  );
}
