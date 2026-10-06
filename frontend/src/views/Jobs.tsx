import { PageHeader } from "../components/PageHeader";
import { Button, Card, Drawer, Space, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { get } from "../api";
import { Json } from "../components/Json";

interface JobRow {
  code: string;
  kind: string;
  status: string;
  gen_code: string;
  error: string;
  created_at: string;
  updated_at: string;
}

const ST_COLOR: Record<string, string> = { queued: "blue", running: "gold", done: "green", failed: "red" };
const KIND_LABEL: Record<string, string> = { generate: "生成", regression: "变更回归" };

export function Jobs() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => get<JobRow[]>("/api/jobs?limit=100"),
    refetchInterval: 2000,
  });
  const [detail, setDetail] = useState<JobRow | null>(null);
  const detailQ = useQuery({
    queryKey: ["job", detail?.code],
    queryFn: () => get<JobRow & { payload: Record<string, unknown> }>(`/api/jobs/${detail?.code}`),
    enabled: !!detail,
  });

  return (
    <div>
      <PageHeader title="任务队列" subtitle="生成与回归任务持久化执行 · 崩溃自动重排队" />
      <Card>
        <Table<JobRow>
          rowKey="code"
          size="small"
          scroll={{ x: 760 }}
          pagination={{ pageSize: 15 }}
          loading={jobs.isLoading}
          dataSource={jobs.data ?? []}
          onRow={(r) => ({ onClick: () => setDetail(r), style: { cursor: "pointer" } })}
          columns={[
            { title: "任务", dataIndex: "code", width: 130 },
            { title: "类型", dataIndex: "kind", width: 100, render: (k: string) => <Tag>{KIND_LABEL[k] ?? k}</Tag> },
            {
              title: "状态",
              dataIndex: "status",
              width: 90,
              render: (s: string) => <Tag color={ST_COLOR[s]}>{s}</Tag>,
            },
            { title: "关联生成", dataIndex: "gen_code", width: 130, render: (v: string) => v || "-" },
            { title: "错误", dataIndex: "error", ellipsis: true, render: (v: string) => v || "-" },
            { title: "更新时间", dataIndex: "updated_at", width: 170, render: (v: string) => v?.replace("T", " ").slice(0, 19) },
          ]}
        />
      </Card>
      <Drawer title={detail?.code} open={!!detail} onClose={() => setDetail(null)} width={480}>
        {detailQ.data && (
          <Space direction="vertical" style={{ width: "100%" }} size={12}>
            <Space wrap>
              <Tag>{KIND_LABEL[detailQ.data.kind] ?? detailQ.data.kind}</Tag>
              <Tag color={ST_COLOR[detailQ.data.status]}>{detailQ.data.status}</Tag>
              {detailQ.data.gen_code && <Tag>生成 {detailQ.data.gen_code}</Tag>}
            </Space>
            <Typography.Title level={5} style={{ marginBottom: 0 }}>
              参数
            </Typography.Title>
            <Json data={detailQ.data.payload} maxHeight={360} />
            {detailQ.data.error && (
              <>
                <Typography.Title level={5} style={{ marginBottom: 0, color: "#cf1322" }}>
                  错误
                </Typography.Title>
                <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12, color: "#cf1322" }}>{detailQ.data.error}</pre>
              </>
            )}
          </Space>
        )}
      </Drawer>
    </div>
  );
}
