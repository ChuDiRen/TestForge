import { Card, Empty, Steps, Tag } from "antd";

export function Placeholder({ view, milestone }: { view: string; milestone: number }) {
  return (
    <Card>
      <Empty description={`「${view}」视图将在 M${milestone} 上线`} style={{ marginTop: 48 }} />
      <Steps
        style={{ maxWidth: 720, margin: "32px auto" }}
        current={milestone - 1}
        items={[
          { title: "M1 单仓闭环" },
          { title: "M2 Wiki 层" },
          { title: "M3 需求+RAG" },
          { title: "M4 契约+多仓" },
          { title: "M5 流程闭环" },
        ]}
      />
      <div style={{ textAlign: "center" }}>
        <Tag color="processing">原型对照 prototype/testforge-prototype.html</Tag>
      </div>
    </Card>
  );
}
