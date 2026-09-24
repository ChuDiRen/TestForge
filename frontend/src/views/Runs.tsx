import { Button, Card, message, Space, Table, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";

interface RunRow {
  code: string;
  target: string;
  layer: string;
  trigger: string;
  sandbox_status: string;
  pass_total: number;
  pass_count: number;
  coverage: number;
  repair_rounds: number;
  cost_s: number;
  status: string;
  trace_id: string;
  req_code: string;
  created_at: string;
}

export function Runs() {
  const qc = useQueryClient();
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => get<RunRow[]>("/api/runs"), refetchInterval: 8000 });
  const rerun = useMutation({
    mutationFn: (code: string) => post(`/api/runs/${code}/rerun`),
    onSuccess: () => {
      message.success("重跑已发起");
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  return (
    <Card title="执行记录（沙箱执行统一台账）">
      <Table<RunRow>
        rowKey="code"
        size="small"
        pagination={{ pageSize: 12 }}
        loading={runs.isLoading}
        dataSource={runs.data ?? []}
        columns={[
          { title: "Run", dataIndex: "code", width: 130 },
          { title: "目标", dataIndex: "target", ellipsis: true },
          { title: "触发", dataIndex: "trigger", width: 80 },
          { title: "沙箱", dataIndex: "sandbox_status", width: 80, render: (s: string) => <Tag>{s}</Tag> },
          {
            title: "通过",
            width: 90,
            render: (_, r) => (
              <Tag color={r.pass_count === r.pass_total ? "green" : "red"}>
                {r.pass_count}/{r.pass_total}
              </Tag>
            ),
          },
          { title: "覆盖率", dataIndex: "coverage", width: 80, render: (v: number) => `${v}%` },
          { title: "修复", dataIndex: "repair_rounds", width: 60, render: (v: number) => `${v}轮` },
          { title: "耗时", dataIndex: "cost_s", width: 70, render: (v: number) => `${v}s` },
          { title: "来源需求", dataIndex: "req_code", width: 100, render: (v: string) => v || "-" },
          { title: "trace", dataIndex: "trace_id", width: 130, ellipsis: true },
          {
            title: "操作",
            width: 80,
            render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => rerun.mutate(r.code)}>
                  重跑
                </Button>
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
