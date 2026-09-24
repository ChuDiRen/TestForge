import { Button, Card, message, Table, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";

interface DefectRow {
  id: number;
  code: string;
  title: string;
  origin_run: string;
  case_codes: string[];
  req_code: string;
  severity: string;
  status: string;
  assignee: string;
  trace_id: string;
}

const SEV_COLOR: Record<string, string> = { 致命: "red", 严重: "orange", 一般: "blue" };
const ST_COLOR: Record<string, string> = { 新建: "default", 已确认: "blue", 修复中: "gold", 待回归: "orange", 已关闭: "green" };

export function Defects() {
  const qc = useQueryClient();
  const defects = useQuery({ queryKey: ["defects"], queryFn: () => get<DefectRow[]>("/api/defects"), refetchInterval: 8000 });
  const act = useMutation({
    mutationFn: ({ id, path }: { id: number; path: string }) => post(`/api/defects/${id}/${path}`, {}),
    onSuccess: (_r, v) => {
      message.success(v.path === "regression" ? "回归完成（只重跑关联用例）" : "状态已更新");
      qc.invalidateQueries({ queryKey: ["defects"] });
    },
  });

  return (
    <Card title="缺陷管理（失败自动建缺陷 · 回归只跑关联用例）">
      <Table<DefectRow>
        rowKey="id"
        size="small"
        pagination={{ pageSize: 12 }}
        loading={defects.isLoading}
        dataSource={defects.data ?? []}
        columns={[
          { title: "编码", dataIndex: "code", width: 110 },
          { title: "标题", dataIndex: "title", ellipsis: true },
          { title: "严重度", dataIndex: "severity", width: 80, render: (s: string) => <Tag color={SEV_COLOR[s]}>{s}</Tag> },
          { title: "状态", dataIndex: "status", width: 90, render: (s: string) => <Tag color={ST_COLOR[s]}>{s}</Tag> },
          { title: "指派", dataIndex: "assignee", width: 110 },
          { title: "来源 run", dataIndex: "origin_run", width: 120, render: (v: string) => v || "-" },
          { title: "需求", dataIndex: "req_code", width: 90, render: (v: string) => v || "-" },
          { title: "关联用例", dataIndex: "case_codes", width: 80, render: (v: string[]) => v?.length ?? 0 },
          {
            title: "操作",
            width: 200,
            render: (_, d) => (
              <>
                {d.status === "新建" && (
                  <Button size="small" onClick={() => act.mutate({ id: d.id, path: "status" })} disabled style={{ marginRight: 6 }}>
                    确认
                  </Button>
                )}
                {d.status !== "已关闭" && (
                  <Button size="small" type="primary" onClick={() => act.mutate({ id: d.id, path: "regression" })} loading={act.isPending}>
                    回归验证
                  </Button>
                )}
              </>
            ),
          },
        ]}
      />
    </Card>
  );
}
