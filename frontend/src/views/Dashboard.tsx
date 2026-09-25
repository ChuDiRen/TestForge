import { Card, Col, Row, Statistic, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";

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

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="接入仓库" value={s?.repos ?? 0} suffix="个" />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="用例总数" value={s?.cases_total ?? 0} suffix="条" />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="执行通过率" value={s?.runs_pass_rate ?? 0} precision={1} suffix="%" />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="Wiki 健康"
              value={(s?.wiki_pages ?? 0) - (s?.wiki_stale ?? 0)}
              suffix={`/ ${s?.wiki_pages ?? 0} 页`}
            />
          </Card>
        </Col>
      </Row>

      <Card
        title="服务状态（gateway → gRPC 全链路）"
        style={{ marginTop: 16 }}
        extra={
          services.data ? (
            <Tag color={services.data.all_green ? "green" : "red"}>
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
            { title: "服务", dataIndex: "name" },
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

      <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
        里程碑视图按 M1~M5 渐次上线；本页数据全部来自真实接口。
      </Typography.Paragraph>
    </div>
  );
}
