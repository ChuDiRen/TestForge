import { useState } from "react";
import { Button, Card, Drawer, Input, Select, Steps, Table, Tag, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { useIsMobile } from "../hooks";

interface ReqRow {
  id: number;
  code: string;
  title: string;
  status: string;
  testability: number;
  repo_id: number | null;
  report: { pipeline?: string[]; rules?: any[]; conflict_detail?: string };
}

const STATUS_COLOR: Record<string, string> = {
  解析中: "blue",
  待人审: "orange",
  规则冲突待确认: "red",
  已生效: "green",
  已打回: "default",
};

export function Requirements() {
  const qc = useQueryClient();
  const [form, setForm] = useState({ title: "", body: "", repo_id: 0 });
  const [pipeline, setPipeline] = useState<string[]>([]);
  const [detail, setDetail] = useState<ReqRow | null>(null);
  const isMobile = useIsMobile();
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  const reqs = useQuery({ queryKey: ["reqs"], queryFn: () => get<{ items: ReqRow[] }>("/api/requirements"), refetchInterval: 6000 });

  const ingest = useMutation({
    mutationFn: () => post<any>("/api/requirements/ingest", form),
    onSuccess: (r) => {
      message.success(`${r.code} 解析完成：${r.status}（可测性 ${Math.round(r.testability)}）`);
      setPipeline(r.report?.pipeline ?? []);
      qc.invalidateQueries({ queryKey: ["reqs"] });
    },
  });
  const confirm = useMutation({
    mutationFn: (id: number) => post<any>(`/api/requirements/${id}/confirm`, { action: "approve" }),
    onSuccess: (r) => {
      message.success(`${r.code} 已生效，自动编排 ${r.generation_id}`);
      qc.invalidateQueries({ queryKey: ["reqs"] });
    },
  });

  return (
    <div>
      <Card title="需求录入（全流程唯一源头）" style={{ marginBottom: 16 }}>
        <Input placeholder="需求标题" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} style={{ marginBottom: 8 }} />
        <Input.TextArea
          rows={4}
          placeholder={"用户故事 + 验收条件…\n例：作为买家，我希望下单数量受上限保护。\n验收条件：数量 1~999 允许下单；禁用用户应被拒绝。"}
          value={form.body}
          onChange={(e) => setForm({ ...form, body: e.target.value })}
          style={{ marginBottom: 8 }}
        />
        <Select
          style={{ width: 260, maxWidth: "100%", marginRight: 8 }}
          placeholder="绑定仓库（必选）"
          value={form.repo_id || undefined}
          onChange={(v) => setForm({ ...form, repo_id: v })}
          options={(repos.data ?? []).map((r) => ({ value: r.id, label: `#${r.id} ${String(r.url).split("/").pop()}` }))}
        />
        <Button type="primary" loading={ingest.isPending} disabled={!form.title || !form.body || !form.repo_id} onClick={() => ingest.mutate()}>
          录入并解析（四步管线）
        </Button>
        {pipeline.length > 0 && (
          <Steps
            size="small"
            style={{ marginTop: 16, maxWidth: 760 }}
            current={pipeline.length}
            items={pipeline.map((p) => ({ title: p.split("(")[0], description: p }))}
          />
        )}
      </Card>
      <Card title="需求列表">
        <Table<ReqRow>
          rowKey="id"
          size="small"
          scroll={{ x: 560 }}
          pagination={{ pageSize: 10 }}
          loading={reqs.isLoading}
          dataSource={reqs.data?.items ?? []}
          onRow={(r) => ({ onClick: () => setDetail(r), style: { cursor: "pointer" } })}
          columns={[
            { title: "编码", dataIndex: "code", width: 90 },
            { title: "标题", dataIndex: "title", ellipsis: true },
            { title: "可测性", dataIndex: "testability", width: 80, render: (v: number) => Math.round(v) },
            {
              title: "状态",
              dataIndex: "status",
              width: 130,
              render: (s: string) => <Tag color={STATUS_COLOR[s] ?? "blue"}>{s}</Tag>,
            },
            {
              title: "操作",
              width: 180,
              render: (_, r) =>
                r.status === "待人审" || r.status === "规则冲突待确认" ? (
                  <Button
                    size="small"
                    type="primary"
                    onClick={(e) => {
                      e.stopPropagation();
                      confirm.mutate(r.id);
                    }}
                  >
                    确认并编排
                  </Button>
                ) : null,
            },
          ]}
        />
      </Card>
      <Drawer title={detail?.title} open={!!detail} onClose={() => setDetail(null)} width={isMobile ? "100%" : 560}>
        {detail && (
          <>
            <p>
              <Tag>{detail.code}</Tag>
              <Tag color={STATUS_COLOR[detail.status]}>{detail.status}</Tag>
              <Tag>可测性 {Math.round(detail.testability)}</Tag>
            </p>
            {detail.report?.conflict_detail && (
              <div style={{ background: "#fff1f0", border: "1px solid #ffa39e", padding: 8, borderRadius: 6, marginBottom: 12 }}>
                {detail.report.conflict_detail}
              </div>
            )}
            <b>抽取规则</b>
            <ul>
              {(detail.report?.rules ?? []).map((r: any, i: number) => (
                <li key={i}>
                  {r.rule} <Tag style={{ marginLeft: 4 }}>{r.kind}</Tag>
                </li>
              ))}
            </ul>
          </>
        )}
      </Drawer>
    </div>
  );
}
