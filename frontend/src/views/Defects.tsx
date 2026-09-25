import { Button, Card, Drawer, message, Space, Table, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { get, post } from "../api";
import { Markdown } from "../components/Markdown";

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
  has_suggestion: boolean;
}

const SEV_COLOR: Record<string, string> = { 致命: "red", 严重: "orange", 一般: "blue" };
const ST_COLOR: Record<string, string> = { 新建: "default", 已确认: "blue", 修复中: "gold", 待回归: "orange", 已关闭: "green" };

export function Defects() {
  const qc = useQueryClient();
  const defects = useQuery({ queryKey: ["defects"], queryFn: () => get<DefectRow[]>("/api/defects"), refetchInterval: 8000 });
  const [suggestFor, setSuggestFor] = useState<DefectRow | null>(null);
  const act = useMutation({
    mutationFn: ({ id, path }: { id: number; path: string }) => post(`/api/defects/${id}/${path}`, {}),
    onSuccess: (_r, v) => {
      message.success(v.path === "regression" ? "回归完成（只重跑关联用例）" : v.path === "suggest" ? "AI 修复建议已生成" : "状态已更新");
      qc.invalidateQueries({ queryKey: ["defects"] });
    },
  });
  const suggestion = useQuery({
    queryKey: ["suggestion", suggestFor?.id],
    queryFn: () => get<{ code: string; suggestion: string }>(`/api/defects/${suggestFor?.id}/suggestion`),
    enabled: !!suggestFor,
  });

  return (
    <div>
      <Card title="缺陷管理（失败自动建缺陷 · 回归只跑关联用例 · AI 修复建议）">
        <Table<DefectRow>
          rowKey="id"
          size="small"
          scroll={{ x: 980 }}
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
              width: 260,
              render: (_, d) => (
                <Space size={4} wrap>
                  {d.status !== "已关闭" && (
                    <Button size="small" type="primary" onClick={() => act.mutate({ id: d.id, path: "regression" })} loading={act.isPending}>
                      回归验证
                    </Button>
                  )}
                  <Button
                    size="small"
                    onClick={() => {
                      setSuggestFor(d);
                      if (!d.has_suggestion) act.mutate({ id: d.id, path: "suggest" });
                    }}
                  >
                    AI 建议{d.has_suggestion ? "✓" : ""}
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Drawer title={`AI 修复建议 · ${suggestFor?.code ?? ""}`} open={!!suggestFor} onClose={() => setSuggestFor(null)} width={520}>
        {suggestion.isFetching && !suggestion.data?.suggestion && <Tag color="processing">DeepSeek 分析中…</Tag>}
        {suggestion.data?.suggestion ? (
          <Markdown>{suggestion.data.suggestion}</Markdown>
        ) : (
          !suggestion.isFetching && <Tag>暂无建议，点「AI 建议」生成</Tag>
        )}
      </Drawer>
    </div>
  );
}
