import { PageHeader } from "../components/PageHeader";
import { useEffect, useState } from "react";
import { Alert, Button, Card, Col, Input, Popconfirm, Progress, Row, Segmented, Select, Space, Steps, Table, Tag, message } from "antd";
import { NextStep } from "../components/NextStep";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, sseUrl } from "../api";
import { Json } from "../components/Json";

const STAGES = ["plan", "guard", "codegen", "sandbox", "coverage"];

interface BatchResult {
  queued: number;
  targets: string[];
}

interface LibraryCase {
  id: number;
  code: string;
  title: string;
  category: string;
  status: string;
  confidence: number;
  target_function: string;
}

const CAT_COLOR: Record<string, string> = {
  normal: "blue",
  boundary: "cyan",
  exception: "orange",
  permission: "red",
  contract: "purple",
};

/** 当前生成批次入库后的真实用例（数据源 = 用例库 API，非 SSE 临时产物） */
function GeneratedCases({ genId }: { genId: string | null }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<LibraryCase | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const library = useQuery({
    queryKey: ["library-cases", genId],
    queryFn: () => get<{ items: LibraryCase[]; total: number }>("/api/cases?page=1&page_size=200"),
    enabled: !!genId,
    refetchInterval: 3000,
  });
  const rows = (library.data?.items ?? []).filter((c) => genId && c.code.includes(genId.slice(-6)));

  const del = useMutation({
    mutationFn: (id: number) => fetch(`/api/cases/${id}`, { method: "DELETE" }).then((r) => r.json()),
    onSuccess: () => {
      message.success("用例已删除");
      qc.invalidateQueries({ queryKey: ["library-cases"] });
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
  const update = useMutation({
    mutationFn: ({ id, title }: { id: number; title: string }) =>
      fetch(`/api/cases/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) }).then((r) => r.json()),
    onSuccess: () => {
      message.success("用例已更新");
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["library-cases"] });
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });

  if (!genId) return null;
  return (
    <Card
      title={`本次生成入库用例（${rows.length} 条 · 与用例库同源）`}
      size="small"
      style={{ marginTop: 16 }}
      extra={<Button size="small" onClick={() => (window.location.search = "?view=cases")}>去用例库 →</Button>}
    >
      <Table<LibraryCase>
        rowKey="id"
        size="small"
        scroll={{ x: 640 }}
        pagination={false}
        loading={library.isLoading}
        dataSource={rows}
        columns={[
          { title: "编码", dataIndex: "code", width: 190 },
          { title: "标题", dataIndex: "title", ellipsis: true },
          { title: "类别", dataIndex: "category", width: 100, render: (c: string) => <Tag color={CAT_COLOR[c]}>{c}</Tag> },
          { title: "置信度", dataIndex: "confidence", width: 80, render: (v: number) => v?.toFixed(2) },
          {
            title: "状态",
            dataIndex: "status",
            width: 90,
            render: (s: string) => <Tag color={s === "已入库" ? "green" : s === "已替换" ? "default" : "orange"}>{s}</Tag>,
          },
          {
            title: "操作",
            width: 150,
            render: (_, c) => (
              <Space size={4}>
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
      {editing && (
        <Card size="small" style={{ marginTop: 8 }} title={`编辑 ${editing.code}`}>
          <Space.Compact style={{ width: "100%", maxWidth: 560 }}>
            <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
            <Button type="primary" onClick={() => update.mutate({ id: editing.id, title: editTitle })} loading={update.isPending}>
              保存
            </Button>
            <Button onClick={() => setEditing(null)}>取消</Button>
          </Space.Compact>
        </Card>
      )}
    </Card>
  );
}

const LAYERS = [
  { value: "ut", label: "单元测试", desc: "选目标函数，AI 六路上下文两阶段生成 + 沙箱执行修复，断言函数级行为" },
  { value: "fn", label: "功能测试", desc: "同一函数级管线，按业务场景语义出用例（主流程/边界/异常/权限），layer=fn 归档" },
  { value: "api", label: "接口测试", desc: "抓取运行中网关的实时 OpenAPI 契约，生成 requests 接口用例（边界/异常/鉴权）" },
  { value: "e2e", label: "E2E 测试", desc: "基于平台真实服务旅程生成端到端脚本，对运行中服务发起真实请求" },
];

export function Workbench() {
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<any[]>("/api/repos") });
  const [repoId, setRepoId] = useState<number>();
  const fns = useQuery({
    queryKey: ["functions", repoId],
    queryFn: () => get<any[]>(`/api/functions?repo_id=${repoId}`),
    enabled: !!repoId,
  });
  const [fn, setFn] = useState<string>();
  const [layer, setLayer] = useState<string>("ut");
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState(-1);
  const [logs, setLogs] = useState<string[]>([]);
  const [result, setResult] = useState<any>(null);
  const [genId, setGenId] = useState<string | null>(null);
  const [batchModule, setBatchModule] = useState<string>();
  const [batchResult, setBatchResult] = useState<BatchResult | null>(null);

  useEffect(() => setFn(undefined), [repoId]);

  const modules = Array.from(new Set((fns.data ?? []).map((f: any) => f.module)));

  const start = async () => {
    if (!repoId) return;
    const isWeb = layer === "api" || layer === "e2e";
    if (!isWeb && !fn) return;
    setRunning(true);
    setStage(0);
    setLogs([]);
    setResult(null);
    setGenId(null);
    try {
      const gen = await post<{ generation_id: string; job_code: string; trace_id: string }>("/api/generations", {
        function: isWeb ? `web-${layer}` : fn,
        repo_id: repoId,
        layer,
      });
      message.info(`生成任务 ${gen.generation_id} 已入队（${gen.job_code}）`);
      const es = new EventSource(sseUrl(`/api/generations/${gen.generation_id}/events`));
      es.addEventListener("stage", (ev) => {
        const d = JSON.parse((ev as MessageEvent).data);
        setLogs((l) => [...l, `[${d.stage}] ${d.message}`]);
        const idx = STAGES.indexOf(d.stage);
        if (idx >= 0) setStage(idx + 1);
      });
      es.addEventListener("result", (ev) => {
        const d = JSON.parse((ev as MessageEvent).data);
        setLogs((l) => [...l, `[result] ${d.message}`]);
        setResult(JSON.parse(d.payload_json || "{}"));
        setGenId(gen.generation_id);
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
      <PageHeader title="生成工作台" subtitle="六路上下文 + 两阶段生成 + 沙箱验证闭环" />
      <Card title="生成新用例（四层）" style={{ marginBottom: 16 }}>
        <Segmented
          value={layer}
          onChange={(v) => {
            setLayer(v as string);
            setFn(undefined);
            setResult(null);
          }}
          options={LAYERS.map((l) => ({ value: l.value, label: l.label }))}
          style={{ marginBlockEnd: 10 }}
        />
        <Alert
          type="info"
          showIcon
          style={{ marginBlockEnd: 12 }}
          message={LAYERS.find((l) => l.value === layer)?.desc}
        />
        <Space wrap>
          <Select
            style={{ width: 240, maxWidth: "100%" }}
            placeholder="选择仓库"
            value={repoId}
            onChange={setRepoId}
            disabled={layer === "api" || layer === "e2e"}
            options={(repos.data ?? []).map((r) => ({ value: r.id, label: `#${r.id} ${String(r.url).split("/").pop()}` }))}
          />
          <Select
            style={{ width: 320, maxWidth: "100%" }}
            value={fn}
            onChange={setFn}
            showSearch
            optionFilterProp="label"
            disabled={layer === "api" || layer === "e2e"}
            placeholder={layer === "api" ? "目标：网关实时 OpenAPI 契约（自动抓取）" : layer === "e2e" ? "目标：平台真实旅程（自动发现）" : "选择目标函数"}
            options={(fns.data ?? []).map((f) => ({ value: f.name, label: `${f.name} (${f.module})` }))}
          />
          <Button type="primary" loading={running} disabled={!repoId || !(layer === "api" || layer === "e2e" || fn)} onClick={start}>
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
      <GeneratedCases genId={genId} />
      {result && (
        <NextStep
          title={`生成完成：通过 ${result.passed}/${result.total}，覆盖率 ${result.coverage}%——用例已按层入库`}
          actions={[
            { label: "去用例库查看", view: "cases" },
            { label: "看执行记录", view: "runs" },
          ]}
        />
      )}
      {result && (
        <Card title="闭环结果" size="small" style={{ marginTop: 16 }}>
          <Row gutter={12}>
            <Col span={24}>
              <Space size="large" wrap>
                <Tag color="green">通过 {result.passed}/{result.total}</Tag>
                <Tag color="blue">覆盖率 {result.coverage}%</Tag>
                <Tag>修复 {result.repair_rounds} 轮</Tag>
                <Tag>trace {result.trace_id}</Tag>
              </Space>
            </Col>
          </Row>
          <Json data={result} maxHeight={240} />
        </Card>
      )}
    </div>
  );
}
