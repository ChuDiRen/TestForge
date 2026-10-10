import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Tabs } from "antd";
import { KnowledgeGraph } from "./KnowledgeGraph";
import { DocKGView } from "./DocKGView";

type GraphTab = "code" | "doc";

/** 知识图谱双视图 Hub：代码图谱（Sigma 调用图/影响面/执行流）与文档图谱（LightRAG 实体/关系/社区）
 *  合并为一个入口的两个 Tab——原先拆成「知识图谱」「文档图谱」两个菜单项属于重复功能。
 *  Tab 内容懒渲染（antd Tabs 首次激活才挂载，Sigma 初始化成本延后），激活后保持挂载不重复初始化。 */
export function KnowledgeGraphHub() {
  const [tab, setTab] = useState<GraphTab>("code");
  return (
    <div>
      <PageHeader
        title="知识图谱"
        subtitle="代码图谱（函数 / 调用边 / 影响面 / 执行流）与文档图谱（实体 / 关系 / 社区）——同一页双视图切换"
      />
      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as GraphTab)}
        items={[
          { key: "code", label: "代码图谱", children: <KnowledgeGraph embedded /> },
          { key: "doc", label: "文档图谱", children: <DocKGView /> },
        ]}
      />
    </div>
  );
}
