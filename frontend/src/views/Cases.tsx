import { useState } from "react";
import { Card, Drawer, Table, Tabs, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";
import { useIsMobile } from "../hooks";

interface CaseRow {
  id: number;
  code: string;
  layer: string;
  title: string;
  module: string;
  category: string;
  status: string;
  source_req: string;
  trace_id: string;
  schema: any;
}

const CAT_COLOR: Record<string, string> = {
  normal: "blue",
  boundary: "cyan",
  exception: "orange",
  permission: "red",
  contract: "purple",
};

export function Cases() {
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => get<{ items: CaseRow[]; by_layer: Record<string, number>; by_category: Record<string, number> }>("/api/cases"), refetchInterval: 10000 });
  const isMobile = useIsMobile();
  const [detail, setDetail] = useState<CaseRow | null>(null);
  const [filters, setFilters] = useState<{ category?: string; status?: string }>({});

  const data = (cases.data?.items ?? []).filter(
    (c) => (!filters.category || c.category === filters.category) && (!filters.status || c.status === filters.status)
  );
  const tab = (layer: string) => (
    <Table<CaseRow>
      rowKey="id"
      size="small"
      scroll={{ x: 560 }}
      pagination={{ pageSize: 10 }}
      dataSource={data.filter((c) => c.layer === layer)}
      onRow={(r) => ({ onClick: () => setDetail(r), style: { cursor: "pointer" } })}
      columns={[
        { title: "编码", dataIndex: "code", width: 170 },
        { title: "标题", dataIndex: "title", ellipsis: true },
        { title: "类别", dataIndex: "category", width: 100, render: (c: string) => <Tag color={CAT_COLOR[c]}>{c}</Tag> },
        { title: "状态", dataIndex: "status", width: 90, render: (s: string) => <Tag color={s === "已入库" ? "green" : "orange"}>{s}</Tag> },
        { title: "来源需求", dataIndex: "source_req", width: 100 },
      ]}
    />
  );

  return (
    <div>
      <Card
        title="用例库（五层，全部可执行 schema）"
        extra={
          <span style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {Object.entries(cases.data?.by_category ?? {}).map(([k, v]) => (
              <Tag key={k} color={CAT_COLOR[k]} style={{ cursor: "pointer" }} onClick={() => setFilters((f) => ({ ...f, category: f.category === k ? undefined : k }))}>
                {k} {v}
              </Tag>
            ))}
          </span>
        }
      >
        <Tabs
          items={[
            { key: "ut", label: `单元测试 (${cases.data?.by_layer?.ut ?? 0})`, children: tab("ut") },
            { key: "api", label: `接口测试 (${cases.data?.by_layer?.api ?? 0})`, children: tab("api") },
            { key: "fn", label: `功能测试 (${cases.data?.by_layer?.fn ?? 0})`, children: tab("fn") },
            { key: "e2e", label: `E2E (${cases.data?.by_layer?.e2e ?? 0})`, children: tab("e2e") },
            { key: "contract", label: `契约 (${cases.data?.by_layer?.contract ?? 0})`, children: tab("contract") },
          ]}
        />
      </Card>
      <Drawer title={detail?.title} open={!!detail} onClose={() => setDetail(null)} width={isMobile ? "100%" : 620}>
        {detail && (
          <>
            <p>
              <Tag>{detail.code}</Tag>
              <Tag color={CAT_COLOR[detail.category]}>{detail.category}</Tag>
              <Tag color={detail.status === "已入库" ? "green" : "orange"}>{detail.status}</Tag>
              <Tag>trace {detail.trace_id}</Tag>
            </p>
            <pre className="json-pre" style={{ background: "#f6f8fa", padding: 12, borderRadius: 6, fontSize: 12 }}>{JSON.stringify(detail.schema, null, 2)}</pre>
          </>
        )}
      </Drawer>
    </div>
  );
}
