import { useState } from "react";
import { Button, Card, Form, Input, Table, Tag, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";

interface RepoRow {
  id: number;
  url: string;
  branch: string;
  status: string;
  last_pull: string | null;
  head_rev: string;
}

export function RepoAdd() {
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => get<RepoRow[]>("/api/repos"), refetchInterval: 8000 });
  const [steps, setSteps] = useState<Record<number, string[]>>({});

  const add = useMutation({
    mutationFn: (v: { url: string; branch: string }) => post<any>("/api/repos", v),
    onSuccess: (r) => {
      message.success(`仓库 #${r.id} 已接入`);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["repos"] });
      setSteps((s) => ({ ...s, [r.id]: r.steps ?? [] }));
    },
  });
  const pull = useMutation({
    mutationFn: (id: number) => post<any>(`/api/repos/${id}/pull`),
    onSuccess: (r) => {
      message.success("拉取完成");
      qc.invalidateQueries({ queryKey: ["repos"] });
      setSteps((s) => ({ ...s, [r.id!]: r.steps ?? [] }));
    },
  });

  return (
    <div>
      <Card title="接入仓库（Git URL / 本地路径 / file://）" style={{ marginBottom: 16 }}>
        <Form form={form} layout="vertical" onFinish={(v) => add.mutate(v)}>
          <Form.Item name="url" rules={[{ required: true, message: "仓库地址必填" }]} style={{ marginBottom: 8 }}>
            <Input style={{ width: 420, maxWidth: "100%" }} placeholder="https://… 或 file:///path/to/repo" />
          </Form.Item>
          <Form.Item name="branch" initialValue="main" style={{ marginBottom: 8 }}>
            <Input style={{ width: 120, maxWidth: "100%" }} placeholder="分支" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={add.isPending}>
            接入（克隆→索引→Wiki 编译）
          </Button>
        </Form>
      </Card>
      <Card title="仓库列表">
        <Table<RepoRow>
          rowKey="id"
          size="small"
          scroll={{ x: 720 }}
          pagination={false}
          loading={repos.isLoading}
          dataSource={repos.data ?? []}
          columns={[
            { title: "ID", dataIndex: "id", width: 60 },
            { title: "URL", dataIndex: "url", ellipsis: true },
            { title: "分支", dataIndex: "branch", width: 80 },
            { title: "HEAD", dataIndex: "head_rev", width: 100, render: (v: string) => v?.slice(0, 8) },
            {
              title: "状态",
              dataIndex: "status",
              width: 100,
              render: (s: string) => <Tag color={s === "已接入" ? "green" : "red"}>{s}</Tag>,
            },
            { title: "最近拉取", dataIndex: "last_pull", width: 160, render: (v: string | null) => v?.replace("T", " ").slice(0, 19) ?? "-" },
            {
              title: "操作",
              width: 100,
              render: (_, r) => (
                <Button size="small" onClick={() => pull.mutate(r.id)} loading={pull.isPending}>
                  拉取
                </Button>
              ),
            },
          ]}
        />
        {Object.entries(steps).map(([id, st]) =>
          st.length ? (
            <Card key={id} size="small" title={`仓库 #${id} 流水线`} style={{ marginTop: 12 }}>
              {st.map((s, i) => (
                <Tag key={i} style={{ marginBottom: 4 }}>
                  {i + 1}. {s}
                </Tag>
              ))}
            </Card>
          ) : null
        )}
      </Card>
    </div>
  );
}
