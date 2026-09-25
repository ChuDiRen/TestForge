import { useState } from "react";
import { Button, Card, Drawer, Select, Space, Table, Tag, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { useIsMobile } from "../hooks";

interface PlanRow {
  code: string;
  version: string;
  req_codes: string[];
  entry_status: string;
  exit_status: string;
  has_report: boolean;
  case_stats?: any;
}

export function Plans() {
  const qc = useQueryClient();
  const reqs = useQuery({ queryKey: ["reqs"], queryFn: () => get<{ items: any[] }>("/api/requirements") });
  const plans = useQuery({ queryKey: ["plans"], queryFn: () => get<PlanRow[]>("/api/plans"), refetchInterval: 8000 });
  const [sel, setSel] = useState<string[]>([]);
  const [detail, setDetail] = useState<PlanRow | null>(null);
  const isMobile = useIsMobile();
  const detailQ = useQuery({
    queryKey: ["plan", detail?.code],
    queryFn: () => get<any>(`/api/plans/${detail!.code}`),
    enabled: !!detail,
  });
  const create = useMutation({
    mutationFn: () => post<any>("/api/plans", { version: `v1.${sel.length}`, req_codes: sel }),
    onSuccess: (r) => {
      message.success(`迭代 ${r.code} 建好：准入 ${r.entry_ok ? "PASS" : "FAIL"}`);
      qc.invalidateQueries({ queryKey: ["plans"] });
    },
  });
  const report = useMutation({
    mutationFn: (code: string) => post<any>(`/api/reports/${code}`),
    onSuccess: (r) => {
      message.success("测试报告已生成");
      qc.invalidateQueries({ queryKey: ["plans"] });
      setDetail((d) => (d ? { ...d, has_report: true } : d));
    },
  });

  return (
    <div>
      <Card
        title="测试计划（以迭代为纲，准入/准出自动判定）"
        style={{ marginBottom: 16 }}
        extra={
          <Space wrap>
            <Select mode="multiple" style={{ width: 420, maxWidth: "100%" }} placeholder="关联需求" value={sel} onChange={setSel}
              options={(reqs.data?.items ?? []).map((r) => ({ value: r.code, label: `${r.code} ${r.title}` }))} />
            <Button type="primary" disabled={!sel.length} onClick={() => create.mutate()} loading={create.isPending}>
              建迭代并核对准入
            </Button>
          </Space>
        }
      >
        <Table<PlanRow>
          rowKey="code"
          size="small"
          scroll={{ x: 640 }}
          pagination={false}
          loading={plans.isLoading}
          dataSource={plans.data ?? []}
          onRow={(r) => ({ onClick: () => setDetail(r), style: { cursor: "pointer" } })}
          columns={[
            { title: "迭代", dataIndex: "code", width: 120 },
            { title: "版本", dataIndex: "version", width: 90 },
            { title: "关联需求", dataIndex: "req_codes", ellipsis: true, render: (v: string[]) => v.join(", ") },
            { title: "准入", dataIndex: "entry_status", width: 100, render: (s: string) => <Tag color={s === "已准入" ? "green" : s === "已打回" ? "red" : "orange"}>{s}</Tag> },
            { title: "准出", dataIndex: "exit_status", width: 100, render: (s: string) => <Tag color={s === "已准出" ? "green" : "orange"}>{s}</Tag> },
            { title: "报告", dataIndex: "has_report", width: 80, render: (v: boolean) => (v ? <Tag color="blue">已生成</Tag> : "-") },
          ]}
        />
      </Card>
      <Drawer title={`迭代 ${detail?.code}`} open={!!detail} onClose={() => setDetail(null)} width={isMobile ? "100%" : 640}>
        {detailQ.data && (
          <>
            <Space style={{ marginBottom: 12 }}>
              <Tag color={detailQ.data.entry_ok ? "green" : "red"}>准入 {detailQ.data.entry_ok ? "PASS" : "FAIL"}</Tag>
              <Tag color={detailQ.data.exit_ok ? "green" : "red"}>准出 {detailQ.data.exit_ok ? "PASS" : "FAIL"}</Tag>
              <Button size="small" type="primary" onClick={() => report.mutate(detail!.code)} loading={report.isPending}>
                生成测试报告
              </Button>
            </Space>
            <b>核对项</b>
            <ul>
              {(detailQ.data.checks ?? []).map((c: string, i: number) => (
                <li key={i} style={{ fontFamily: "monospace" }}>
                  {c}
                </li>
              ))}
            </ul>
            {detailQ.data.report && (
              <>
                <b>测试报告</b>
                <pre className="json-pre" style={{ background: "#f6f8fa", padding: 12, borderRadius: 6, fontSize: 12 }}>
                  {JSON.stringify(detailQ.data.report, null, 2)}
                </pre>
              </>
            )}
          </>
        )}
      </Drawer>
    </div>
  );
}
