import { useState } from "react";
import { Card, Col, Row, Table, Tag } from "antd";
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

/** 服务依赖图（静态布局，边标契约名+版本；breaking 红色） */
function ServiceMap({ contracts, onImpact }: { contracts: ContractRow[]; onImpact: (c: ContractRow) => void }) {
  const nodes = [
    { id: "frontend", x: 80, y: 150, label: "前端" },
    { id: "gateway", x: 230, y: 150, label: "gateway" },
    { id: "order-svc", x: 420, y: 80, label: "order-svc" },
    { id: "payment-svc", x: 420, y: 220, label: "payment-svc" },
  ];
  return (
    <svg width="100%" height={300} style={{ background: "#fafafa", borderRadius: 6 }}>
      {nodes.map((n) => (
        <g key={n.id}>
          <rect x={n.x - 55} y={n.y - 22} width={110} height={44} rx={8} fill="#4f46e5" opacity={0.92} />
          <text x={n.x} y={n.y + 5} textAnchor="middle" fill="#fff" fontSize={13}>
            {n.label}
          </text>
        </g>
      ))}
      <line x1={135} y1={150} x2={175} y2={150} stroke="#666" strokeWidth={2} markerEnd="url(#arrow)" />
      {contracts.slice(0, 3).map((c, i) => {
        const breaking = c.last_breaking;
        return (
          <g key={c.id} onClick={() => onImpact(c)} style={{ cursor: "pointer" }}>
            <line x1={285} y1={150} x2={365} y2={i === 0 ? 80 : i === 1 ? 220 : 150} stroke={breaking ? "#f5222d" : "#999"} strokeWidth={breaking ? 2.5 : 1.5} strokeDasharray={breaking ? "6 3" : undefined} markerEnd="url(#arrow)" />
            <text x={320} y={i === 0 ? 105 : i === 1 ? 195 : 140} fontSize={11} fill={breaking ? "#f5222d" : "#555"}>
              {c.name}@{c.version}
              {breaking ? " ⚠breaking" : ""}
            </text>
          </g>
        );
      })}
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="2" orient="auto">
          <path d="M0,0 L0,4 L7,2 z" fill="#666" />
        </marker>
      </defs>
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
      <Col span={14}>
        <Card title="服务地图（点 breaking 边看影响分析）">
          <ServiceMap contracts={contracts.data ?? []} onImpact={runImpact} />
        </Card>
      </Col>
      <Col span={10}>
        <Card title="契约中心" size="small">
          <Table<ContractRow>
            rowKey="id"
            size="small"
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
