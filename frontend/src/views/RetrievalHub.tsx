import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Tabs } from "antd";
import { RetrievalLab } from "./RetrievalLab";
import { RagEval } from "./RagEval";
import { SearchBench } from "./SearchBench";

type RetrievalTab = "lab" | "bench" | "eval";

/** 检索中心 Hub：原先「检索实验室 / 检索测试台（知识资产页内嵌）/ 检索质量」三处检索相关入口
 *  收拢为一个页面三个 Tab——查询实验（LightRAG 六模式）· 检索测试台（生成侧七路混合检索预演）
 *  · 检索质量（黄金集 recall@k / MRR 评测）。 */
export function RetrievalHub() {
  const [tab, setTab] = useState<RetrievalTab>("lab");
  return (
    <div>
      <PageHeader
        title="检索中心"
        subtitle="查询实验（六模式图检索）· 检索测试台（生成侧七路混合检索预演）· 检索质量（黄金集评测）"
      />
      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as RetrievalTab)}
        items={[
          { key: "lab", label: "查询实验", children: <RetrievalLab embedded /> },
          { key: "bench", label: "检索测试台", children: <SearchBench /> },
          { key: "eval", label: "检索质量", children: <RagEval embedded /> },
        ]}
      />
    </div>
  );
}
