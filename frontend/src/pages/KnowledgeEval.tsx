import { PageHeader } from '@/components/PageHeader'
import { Tabs } from 'antd'
import { RagEval } from './RagEval'
import { SearchBench } from './SearchBench'

/** 评估与优化（独立入口，原检索中心解散后收拢）：检索质量黄金集评测 + 生成侧七路检索预演。 */
export function KnowledgeEval() {
  return (
    <div>
      <PageHeader title="评估与优化" subtitle="检索质量（黄金集评测）· 检索测试台（生成侧七路混合检索预演）" />
      <Tabs
        defaultActiveKey="quality"
        items={[
          { key: 'quality', label: '检索质量', children: <RagEval embedded /> },
          { key: 'bench', label: '检索测试台', children: <SearchBench /> },
        ]}
      />
    </div>
  )
}
