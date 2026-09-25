import { useEffect, useState } from "react";
import { Button, Card, Col, Progress, Row, Select, Space, Steps, Table, Tag, message } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { get, post, sseUrl } from "../api";
import { Json } from "../components/Json";

const STAGES = ["plan", "guard", "codegen", "sandbox", "coverage"];

interface BatchResult {
  queued: number;
  targets: string[];
}

export function Workbench() {
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  const [repoId, setRepoId] = useState<number>();
  const fns = useQuery({
    queryKey: ["functions", repoId],
    queryFn: () => get<any[]>(`/api/functions?repo_id=${repoId}`),
    enabled: !!repoId,
  });
  const [fn, setFn] = useState<string>();
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState(-1);
  const [logs, setLogs] = useState<string[]>([]);
  const [result, setResult] = useState<any>(null);
  const [cases, setCases] = useState<any[]>([]);
  const [batchModule, setBatchModule] = useState<string>();
  const [batchResult, setBatchResult] = useState<BatchResult | null>(null);

  useEffect(() => setFn(undefined), [repoId]);

  const modules = Array.from(new Set((fns.data ?? []).map((f: any) => f.module)));

  const start = async () => {
    if (!repoId || !fn) return;
    setRunning(true);
    setStage(0);
    setLogs([]);
    setResult(null);
    setCases([]);
    try {
      const gen = await post<{ generation_id: string; job_code: string; trace_id: string }>("/api/generations", {
        function: fn,
        repo_id: repoId,
        layer: "ut",
      });
      message.info(`生成任务 ${gen.generation_id} 已入队（${gen.job_code}）`);
      const es = new EventSource(sseUrl(`/api/generations/${gen.generation_id}/events`));
      es.addEventListener("stage", (ev) => {
        const d = JSON.parse((ev as MessageEvent).data);
        setLogs((l) => [...l, `[${d.stage}] ${d.message}`]);
        const idx = STAGES.indexOf(d.stage);
        if (idx >= 0) setStage(idx + 1);
        if (d.stage === "codegen") {
          try {
            const payload = JSON.parse(d.payload_json);
            setCases(payload.cases ?? []);
          } catch {
            /* ignore */
          }
        }
      });
      es.addEventListener("result", (ev) => {
        const d = JSON.parse((ev as MessageEvent).data);
        setLogs((l) => [...l, `[result] ${d.message}`]);
        setResult(JSON.parse(d.payload_json || "{}"));
        setStage(5);
        setRunning(false);
        es.close();
      });
      es.onerror = () => {
        setRunning(false);
        es.close();
      };
    } catch (e: any) {
      message.error(e.message);
      setRunning(false);
    }
  };

  const batch = useMutation({
    mutationFn: (payload: { repo_id: number; module: string }) =>
      post<BatchResult>("/api/generations/batch", payload),
    onSuccess: (res) => {
      setBatchResult(res);
      message.success(`批量任务已入队：${res.queued} 个函数，进度看「任务队列」`);
    },
    onError: (e: any) => message.error(e.message),
  });

  return (
    <div>
      <Card title="生成工作台（六路上下文 + 两阶段生成 + 沙箱闭环）" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            style={{ width: 240, maxWidth: "100%" }}
            placeholder="选择仓库"
            value={repoId}
            onChange={setRepoId}
            options={(repos.data ?? []).map((r) => ({ value: r.id, label: `#${r.id} ${String(r.url).split("/").pop()}` }))}
          />
          <Select
            style={{ width: 320, maxWidth: "100%" }}
            placeholder="选择目标函数"
            value={fn}
            onChange={setFn}
            showSearch
            optionFilterProp="label"
            options={(fns.data ?? []).map((f) => ({ value: f.name, label: `${f.name} (${f.module})` }))}
          />
          <Button type="primary" loading={running} disabled={!repoId || !fn} onClick={start}>
            ▶ 开始生成
          </Button>
        </Space>
        <Steps
          size="small"
          style={{ marginTop: 20, maxWidth: 860 }}
          current={stage}
          status={running ? "process" : stage >= 5 ? "finish" : "wait"}
          items={[
            { title: "PLAN 清单" },
            { title: "覆盖守卫" },
            { title: "代码生成" },
            { title: "沙箱执行" },
            { title: "覆盖率回填" },
          ]}
        />
        {running && <Progress percent={Math.min(95, stage * 19)} size="small" style={{ maxWidth: 860, marginTop: 8 }} />}
        {logs.length > 0 && (
          <pre style={{ background: "#0b1021", color: "#9ecbff", padding: 12, borderRadius: 6, maxHeight: 180, overflow: "auto", fontSize: 12, marginTop: 12 }}>
            {logs.join("\n")}
          </pre>
        )}
      </Card>
      <Card title="批量生成（按模块圈选 → 任务队列逐个执行，含沙箱验证）" size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            style={{ width: 320, maxWidth: "100%" }}
            placeholder="选择模块（同仓库）"
            value={batchModule}
            onChange={setBatchModule}
            showSearch
            optionFilterProp="label"
            disabled={!repoId}
            options={modules.map((m) => ({ value: m, label: m }))}
          />
          <Button
            type="primary"
            disabled={!repoId || !batchModule}
            loading={batch.isPending}
            onClick={() => repoId && batchModule && batch.mutate({ repo_id: repoId, module: batchModule })}
          >
            ⏫ 批量入队（≤50 个函数）
          </Button>
          {batchResult && <Tag color="blue">已入队 {batchResult.queued} 个：{batchResult.targets.slice(0, 5).join(", ")}{batchResult.targets.length > 5 ? "…" : ""}</Tag>}
        </Space>
      </Card>
      {cases.length > 0 && (
        <Card title={`结构化用例表（${cases.length} 条，点行看可执行 JSON）`} size="small">
          <Table
            rowKey="code"
            size="small"
            scroll={{ x: 460 }}
            pagination={{ pageSize: 10 }}
            dataSource={cases}
            expandable={{
              expandedRowRender: (r: any) => <Json data={r} maxHeight={320} />,
            }}
            columns={[
              { title: "ID", dataIndex: "code", width: 90 },
              { title: "标题", dataIndex: "title", ellipsis: true },
              { title: "类别", dataIndex: "category", width: 110, render: (c: string) => <Tag>{c}</Tag> },
              { title: "置信度", dataIndex: "confidence", width: 80, render: (v: number) => v?.toFixed(2) },
            ]}
          />
        </Card>
      )}
      {result && (
        <Card title="闭环结果" size="small" style={{ marginTop: 16 }}>
          <Row gutter={12}>
            <Col>
              <Space size="large" wrap>
                <Tag color="green">通过 {result.passed}/{result.total}</Tag>
                <Tag color="blue">覆盖率 {result.coverage}%</Tag>
                <Tag>修复 {result.repair_rounds} 轮</Tag>
                <Tag>trace {result.trace_id}</Tag>
              </Space>
            </Col>
          </Row>
        </Card>
      )}
    </div>
  );
}
