import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Button, Card, Input, Table, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";

interface TraceEvent {
  ts: string;
  trace_id: string;
  type: string;
  actor: string;
  summary: string;
  req_code: string;
}

const TYPE_COLOR: Record<string, string> = {
  需求: "purple",
  生成: "blue",
  执行: "green",
  仓库: "cyan",
  契约: "geekblue",
  缺陷: "red",
  计划: "orange",
};

export function Logs() {
  const [tid, setTid] = useState("");
  const tr = useQuery({
    queryKey: ["trace", tid],
    queryFn: () => get<{ events: TraceEvent[] }>(`/api/traces/${tid}`),
    enabled: tid.startsWith("tr_"),
  });

  return (
    <div>
      <PageHeader title="日志 / 追溯" subtitle="traceID 全链路台账 · 写操作全量留痕" />
      <Card
        extra={
        <span style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <Input
            style={{ width: 280, maxWidth: "100%" }}
            placeholder="输入 traceID（tr_xxx）回溯全链路"
            value={tid}
            onChange={(e) => setTid(e.target.value)}
          />
          <Button type="primary" disabled={!tid.startsWith("tr_")}>
            查询
          </Button>
        </span>
      }
    >
      {tid.startsWith("tr_") ? (
        <>
          <div style={{ marginBottom: 12, fontFamily: "monospace" }}>
            {`链路 ${tid}`}
            {(tr.data?.events ?? []).length > 0 && ` · ${tr.data!.events.length} 个事件`}
          </div>
          <Table<TraceEvent>
            rowKey={(_, i) => String(i)}
            size="small"
            scroll={{ x: 640 }}
            pagination={false}
            loading={tr.isFetching}
            dataSource={tr.data?.events ?? []}
            rowClassName={(_, i) => (i === (tr.data?.events.length ?? 0) - 1 ? "" : "")}
            columns={[
              { title: "时间", dataIndex: "ts", width: 170, render: (v: string) => v?.replace("T", " ").slice(0, 19) },
              { title: "类型", dataIndex: "type", width: 80, render: (t: string) => <Tag color={TYPE_COLOR[t]}>{t}</Tag> },
              { title: "操作者", dataIndex: "actor", width: 130 },
              { title: "摘要", dataIndex: "summary" },
              { title: "需求", dataIndex: "req_code", width: 100, render: (v: string) => v || "-" },
            ]}
          />
        </>
      ) : (
        <div style={{ color: "#999" }}>提示：任意用例/执行详情里的 traceID 可回溯「需求录入 → 生成 → 沙箱执行」全链路。</div>
      )}
    </Card>
    </div>
  );
}
