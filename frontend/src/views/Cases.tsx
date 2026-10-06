import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Button, Card, Drawer, Input, Popconfirm, Space, Table, Tabs, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get } from "../api";
import { Json } from "../components/Json";
import { Markdown } from "../components/Markdown";
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
  const qc = useQueryClient();
  const [editing, setEditing] = useState<CaseRow | null>(null);
  const [editTitle, setEditTitle] = useState("");

  const refresh = () => qc.invalidateQueries({ queryKey: ["cases"] });
  const del = useMutation({
    mutationFn: async (id: number) => {
      const r = await fetch(`/api/cases/${id}`, { method: "DELETE" });
      return r.json();
    },
    onSuccess: () => {
      message.success("用例已删除");
      refresh();
    },
  });
  const update = useMutation({
    mutationFn: async ({ id, title }: { id: number; title: string }) => {
      const r = await fetch(`/api/cases/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
      return r.json();
    },
    onSuccess: () => {
      message.success("用例已更新");
      setEditing(null);
      refresh();
    },
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
        {
          title: "状态",
          dataIndex: "status",
          width: 90,
          render: (s: string) => <Tag color={s === "已入库" ? "green" : s === "已替换" ? "default" : "orange"}>{s}</Tag>,
        },
        {
          title: "回归",
          dataIndex: "stale",
          width: 76,
          render: (v: boolean) => (v ? <Tag color="volcano">待回归</Tag> : <Tag>—</Tag>),
        },
        { title: "来源需求", dataIndex: "source_req", width: 100, render: (v: string) => v || "-" },
        {
          title: "操作",
          width: 150,
          render: (_, c) => (
            <Space size={4} onClick={(e) => e.stopPropagation()}>
              <Button
                size="small"
                onClick={() => {
                  setEditing(c);
                  setEditTitle(c.title);
                }}
              >
                改名
              </Button>
              <Popconfirm title="删除该用例？" onConfirm={() => del.mutate(c.id)}>
                <Button size="small" danger loading={del.isPending}>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          ),
        },
      ]}
    />
  );

  return (
    <div>
      <PageHeader title="用例库" subtitle="五层可执行 schema · 服务端分页 · 变更驱动自动标待回归" />
      <Card
        title="用例列表"
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
      {editing && (
        <Card size="small" style={{ marginBottom: 12 }} title={`编辑 ${editing.code}`}>
          <Space.Compact style={{ width: "100%", maxWidth: 560 }}>
            <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
            <Button type="primary" onClick={() => update.mutate({ id: editing.id, title: editTitle })} loading={update.isPending}>
              保存
            </Button>
            <Button onClick={() => setEditing(null)}>取消</Button>
          </Space.Compact>
        </Card>
      )}
      <Drawer title={detail?.title} open={!!detail} onClose={() => setDetail(null)} width={isMobile ? "100%" : 620}>
        {detail && (
          <>
            <p>
              <Tag>{detail.code}</Tag>
              <Tag color={CAT_COLOR[detail.category]}>{detail.category}</Tag>
              <Tag color={detail.status === "已入库" ? "green" : detail.status === "已替换" ? "default" : "orange"}>{detail.status}</Tag>
              {detail.stale && <Tag color="volcano">待回归</Tag>}
              <Tag>trace {detail.trace_id}</Tag>
            </p>
            {(() => {
              // 入库 schema = 元数据 + code_file（整份生成测试源码）。源码不是 JSON，
              // 混在一起会把转义串糊成一坨——拆开：元数据走 JSON 组件，源码单独成块
              const schema = (detail.schema ?? {}) as Record<string, unknown>;
              const { code_file, ...meta } = schema;
              return (
                <>
                  <Json data={meta} maxHeight={340} />
                  {typeof code_file === "string" && code_file.trim() && (
                    <>
                      <Typography.Title level={5} style={{ margin: "14px 0 8px" }}>
                        生成的测试代码
                      </Typography.Title>
                      <Markdown>{`\`\`\`python
${code_file}
\`\`\`}`}</Markdown>
                    </>
                  )}
                </>
              );
            })()}
          </>
        )}
      </Drawer>
    </div>
  );
}
