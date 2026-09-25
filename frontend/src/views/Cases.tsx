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
  stale: boolean;
  schema: any;
}

interface CasesResp {
  total: number;
  page: number;
  page_size: number;
  stale_total: number;
  by_layer: Record<string, number>;
  by_category: Record<string, number>;
  items: CaseRow[];
}

const CAT_COLOR: Record<string, string> = {
  normal: "blue",
  boundary: "cyan",
  exception: "orange",
  permission: "red",
  contract: "purple",
};

const LAYERS = ["ut", "api", "fn", "e2e", "contract"];
const LAYER_LABEL: Record<string, string> = { ut: "单元测试", api: "接口测试", fn: "功能测试", e2e: "E2E", contract: "契约" };

export function Cases() {
  const isMobile = useIsMobile();
  const [layer, setLayer] = useState("ut");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [category, setCategory] = useState<string>();
  const [staleOnly, setStaleOnly] = useState(false);
  const [detail, setDetail] = useState<CaseRow | null>(null);

  const params = new URLSearchParams({
    layer,
    page: String(page),
    page_size: String(pageSize),
  });
  if (category) params.set("category", category);
  if (staleOnly) params.set("stale", "true");

  const cases = useQuery({
    queryKey: ["cases", layer, page, pageSize, category ?? "", staleOnly],
    queryFn: () => get<CasesResp>(`/api/cases?${params.toString()}`),
    refetchInterval: 10000,
    placeholderData: (prev) => prev,
  });

  const tab = (
    <Table<CaseRow>
      rowKey="id"
      size="small"
      scroll={{ x: 620 }}
      loading={cases.isLoading}
      dataSource={cases.data?.items ?? []}
      onRow={(r) => ({ onClick: () => setDetail(r), style: { cursor: "pointer" } })}
      pagination={{
        current: page,
        pageSize,
        total: cases.data?.total ?? 0,
        showSizeChanger: true,
        showTotal: (t) => `共 ${t} 条`,
        onChange: (p, ps) => {
          setPage(p);
          setPageSize(ps);
        },
      }}
      columns={[
        { title: "编码", dataIndex: "code", width: 170 },
        { title: "标题", dataIndex: "title", ellipsis: true },
        { title: "类别", dataIndex: "category", width: 100, render: (c: string) => <Tag color={CAT_COLOR[c]}>{c}</Tag> },
        { title: "状态", dataIndex: "status", width: 90, render: (s: string) => <Tag color={s === "已入库" ? "green" : "orange"}>{s}</Tag> },
        {
          title: "回归",
          dataIndex: "stale",
          width: 76,
          render: (v: boolean) => (v ? <Tag color="volcano">待回归</Tag> : <Tag>—</Tag>),
        },
        { title: "来源需求", dataIndex: "source_req", width: 100 },
      ]}
    />
  );

  return (
    <div>
      <Card
        title={`用例库（五层可执行 schema · 服务端分页${cases.data?.stale_total ? ` · 待回归 ${cases.data.stale_total} 条` : ""}）`}
        extra={
          <span style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
            <Tag
              color={staleOnly ? "volcano" : "default"}
              style={{ cursor: "pointer" }}
              onClick={() => {
                setStaleOnly(!staleOnly);
                setPage(1);
              }}
            >
              待回归 {cases.data?.stale_total ?? 0}
            </Tag>
            {Object.entries(cases.data?.by_category ?? {}).map(([k, v]) => (
              <Tag
                key={k}
                color={category === k ? CAT_COLOR[k] : undefined}
                style={{ cursor: "pointer" }}
                onClick={() => {
                  setCategory(category === k ? undefined : k);
                  setPage(1);
                }}
              >
                {k} {v}
              </Tag>
            ))}
          </span>
        }
      >
        <Tabs
          activeKey={layer}
          onChange={(k) => {
            setLayer(k);
            setPage(1);
          }}
          items={LAYERS.map((lay) => ({
            key: lay,
            label: `${LAYER_LABEL[lay]} (${cases.data?.by_layer?.[lay] ?? 0})`,
            children: lay === layer ? tab : null,
          }))}
        />
      </Card>
      <Drawer title={detail?.title} open={!!detail} onClose={() => setDetail(null)} width={isMobile ? "100%" : 620}>
        {detail && (
          <>
            <p>
              <Tag>{detail.code}</Tag>
              <Tag color={CAT_COLOR[detail.category]}>{detail.category}</Tag>
              <Tag color={detail.status === "已入库" ? "green" : "orange"}>{detail.status}</Tag>
              {detail.stale && <Tag color="volcano">待回归</Tag>}
              <Tag>trace {detail.trace_id}</Tag>
            </p>
            <pre className="json-pre" style={{ background: "#f6f8fa", padding: 12, borderRadius: 6, fontSize: 12 }}>{JSON.stringify(detail.schema, null, 2)}</pre>
          </>
        )}
      </Drawer>
    </div>
  );
}
