import { Card, Col, Row, Table, Tag } from "antd";
import { ApartmentOutlined, BookOutlined, CheckCircleOutlined, DatabaseOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";
import { PageHeader } from "../components/PageHeader";
import { StatCard } from "../components/StatCard";

interface ServiceStatus {
  name: string;
  port: number;
  ok: boolean;
  db_ok: boolean;
  version?: string;
  error?: string;
}

interface Stats {
  repos: number;
  cases_total: number;
  cases_by_layer: Record<string, number>;
  runs_total: number;
  runs_pass_rate: number;
  requirements_total: number;
  defects_open: number;
  wiki_pages: number;
  wiki_stale: number;
  contracts: number;
}

export function Dashboard() {
  const services = useQuery({
    queryKey: ["services"],
    queryFn: () => get<{ services: ServiceStatus[]; all_green: boolean }>("/api/system/services"),
    refetchInterval: 15000,
  });
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => get<Stats>("/api/stats/summary") });

  const s = stats.data;
  const wikiOk = (s?.wiki_pages ?? 0) - (s?.wiki_stale ?? 0);

  return (
    <div>
      <PageHeader
        title="仪表盘"
        subtitle="平台运行全景 · 数据全部来自真实接口，15s 自动刷新服务状态"
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} xl={6}>
          <StatCard
            icon={<ApartmentOutlined />}
            label="接入仓库"
            value={s?.repos ?? 0}
            suffix="个"
            tone="indigo"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <StatCard
            icon={<DatabaseOutlined />}
            label="用例总数"
            value={s?.cases_total ?? 0}
            suffix="条"
            tone="violet"
            footer={`单测/接口/E2E 分层管理${s?.cases_total ? ` · 当前 ${(s?.cases_by_layer?.ut ?? 0) + (s?.cases_by_layer?.api ?? 0) + (s?.cases_by_layer?.e2e ?? 0)} 条在库` : ""}`}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <StatCard
            icon={<CheckCircleOutlined />}
            label="执行通过率"
            value={s?.runs_pass_rate ?? 0}
            suffix="%"
            tone={(s?.runs_pass_rate ?? 0) >= 90 ? "green" : "amber"}
            footer={`累计执行 ${s?.runs_total ?? 0} 次（真实沙箱）`}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <StatCard
            icon={<BookOutlined />}
            label="Wiki 健康"
            value={wikiOk}
            suffix={`/ ${s?.wiki_pages ?? 0} 页`}
            tone="cyan"
            footer={(s?.wiki_stale ?? 0) > 0 ? `${s?.wiki_stale} 页待重建（源码已变更）` : "知识全部新鲜"}
          />
        </Col>
      </Row>

      <Card
        title="服务状态（gateway → gRPC 全链路）"
        style={{ marginTop: 16 }}
        extra={
          services.data ? (
            <Tag color={services.data.all_green ? "green" : "red"} style={{ fontWeight: 500 }}>
              {services.data.all_green ? "全部 GREEN" : "存在 FAIL"}
            </Tag>
          ) : null
        }
      >
        <Table<ServiceStatus>
          rowKey="name"
          size="small"
          scroll={{ x: 620 }}
          pagination={false}
          loading={services.isLoading}
          dataSource={services.data?.services ?? []}
          columns={[
            { title: "服务", dataIndex: "name", render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span> },
            { title: "端口", dataIndex: "port" },
            {
              title: "gRPC",
              dataIndex: "ok",
              render: (v: boolean) => <Tag color={v ? "green" : "red"}>{v ? "UP" : "DOWN"}</Tag>,
            },
            {
              title: "DB",
              dataIndex: "db_ok",
              render: (v: boolean) => <Tag color={v ? "green" : "red"}>{v ? "OK" : "FAIL"}</Tag>,
            },
            { title: "版本", dataIndex: "version" },
            { title: "错误", dataIndex: "error", render: (e?: string) => e ?? "-" },
          ]}
        />
      </Card>
    </div>
  );
}
