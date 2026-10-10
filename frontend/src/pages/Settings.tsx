import { PageHeader } from '@/components/PageHeader'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Descriptions, Input, InputNumber, Select, Space, Switch, Tag, message } from 'antd'
import { ClearOutlined, FileTextOutlined, SaveOutlined } from '@ant-design/icons'
import { del, get, put } from '@/service'
import { useLang, setLang, type Lang } from '@/store'

interface KgSettingsResp {
  settings: Record<string, string>
  dirty: boolean
  embedding: { backend: string; dim: number }
  rerank_enabled: boolean
  websearch_available: boolean
}

/** 系统设置（LightRAG WebUI Settings 对齐 + TestForge 特有缓存/运维）：检索参数运行时覆盖、
 *  LLM 缓存管理、embedding/rerank 状态、界面语言、API 文档入口。 */
export function Settings() {
  const { t, lang } = useLang()
  const qc = useQueryClient()
  const [form, setForm] = useState<Record<string, string>>({})

  const resp = useQuery({ queryKey: ['kg-settings'], queryFn: () => get<KgSettingsResp>('/api/kg/settings') })
  const cacheQ = useQuery({ queryKey: ['llm-cache'], queryFn: () => get<{ total: number; by_role: Record<string, number> }>('/api/llm/cache') })

  useEffect(() => {
    if (resp.data) setForm(resp.data.settings)
  }, [resp.data])

  const save = useMutation({
    mutationFn: () => put('/api/kg/settings', { settings: form }),
    onSuccess: () => {
      message.success(t.settings.saved)
      qc.invalidateQueries({ queryKey: ['kg-settings'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const clearQueryCache = useMutation({
    mutationFn: () => del<{ cleared: number }>('/api/kg/query-cache'),
    onSuccess: (r) => {
      message.success(`cleared ${r.cleared}`)
      cacheQ.refetch()
    },
    onError: (e: Error) => message.error(e.message),
  })
  const clearExtractCache = useMutation({
    mutationFn: (role: string) => del<{ cleared: number }>(`/api/llm/cache?role=${role}`),
    onSuccess: (r) => {
      message.success(`cleared ${r.cleared}`)
      cacheQ.refetch()
    },
    onError: (e: Error) => message.error(e.message),
  })

  const set = (k: string, v: string) => setForm((s) => ({ ...s, [k]: v }))

  return (
    <div>
      <PageHeader title={t.settings.title} subtitle={t.settings.subtitle} />

      <Card
        size="small"
        title={t.settings.retrieval}
        style={{ marginBottom: 16 }}
        extra={
          <Button size="small" type="primary" icon={<SaveOutlined />} loading={save.isPending} onClick={() => save.mutate()}>
            {t.settings.save}
          </Button>
        }
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 14, fontSize: 13 }}>
          <Field label={t.settings.chunkStrategy}>
            <Select
              size="small"
              value={form.chunk_strategy || 'paragraph'}
              onChange={(v) => set('chunk_strategy', v)}
              style={{ width: 180 }}
              options={[
                { value: 'paragraph', label: t.settings.paragraph },
                { value: 'fixed', label: t.settings.fixed },
                { value: 'recursive', label: t.settings.recursive },
                { value: 'vector', label: t.settings.vector },
              ]}
            />
          </Field>
          <Field label={t.settings.chunkSize}>
            <InputNumber
              size="small"
              min={200}
              max={8000}
              step={100}
              value={Number(form.chunk_size || 1200)}
              onChange={(v) => set('chunk_size', String(v ?? 1200))}
            />
          </Field>
          <Field label={t.settings.chunkOverlap}>
            <InputNumber
              size="small"
              min={0}
              max={2000}
              step={50}
              value={Number(form.chunk_overlap || 100)}
              onChange={(v) => set('chunk_overlap', String(v ?? 100))}
            />
          </Field>
          <Field label={t.settings.dropRefs}>
            <Switch
              size="small"
              checked={(form.chunk_drop_references || 'True').toLowerCase() !== 'false'}
              onChange={(v) => set('chunk_drop_references', String(v))}
            />
          </Field>
          <Field label={t.settings.gleaning}>
            <InputNumber
              size="small"
              min={0}
              max={5}
              value={Number(form.gleaning_rounds ?? 1)}
              onChange={(v) => set('gleaning_rounds', String(v ?? 0))}
            />
          </Field>
          <Field label={t.settings.queryCache}>
            <Switch
              size="small"
              checked={(form.query_cache_enabled || 'True').toLowerCase() !== 'false'}
              onChange={(v) => set('query_cache_enabled', String(v))}
            />
          </Field>
          <Field label={t.settings.websearch}>
            <Switch
              size="small"
              checked={(form.websearch_enabled || 'False').toLowerCase() === 'true'}
              onChange={(v) => set('websearch_enabled', String(v))}
            />
          </Field>
          <Field label={t.settings.promptPrefix}>
            <Input
              size="small"
              value={form.user_prompt_prefix || ''}
              onChange={(e) => set('user_prompt_prefix', e.target.value)}
              style={{ width: 260 }}
            />
          </Field>
        </div>
      </Card>

      <Card size="small" title={t.settings.runtime} style={{ marginBottom: 16 }}>
        <Descriptions
          size="small"
          column={{ xs: 1, md: 2, lg: 3 }}
          items={[
            {
              key: 'emb',
              label: t.settings.embedding,
              children: (
                <Tag color={resp.data?.embedding.backend === 'local' ? 'default' : 'green'}>
                  {resp.data?.embedding.backend} · {resp.data?.embedding.dim}d
                </Tag>
              ),
            },
            {
              key: 'rr',
              label: t.settings.rerank,
              children: (
                <Tag color={resp.data?.rerank_enabled ? 'green' : 'default'}>{resp.data?.rerank_enabled ? t.settings.on : t.settings.off}</Tag>
              ),
            },
            {
              key: 'ws',
              label: 'ddgs',
              children: (
                <Tag color={resp.data?.websearch_available ? 'green' : 'default'}>
                  {resp.data?.websearch_available ? t.settings.websearchAvail : t.settings.off}
                </Tag>
              ),
            },
            {
              key: 'dirty',
              label: 'communities',
              children: resp.data?.dirty ? <Tag color="orange">stale（重建可刷新）</Tag> : <Tag color="green">fresh</Tag>,
            },
          ]}
        />
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--tf-ink-3)' }}>{t.settings.rebuildHint}</div>
      </Card>

      <Card size="small" title={t.settings.cache} style={{ marginBottom: 16 }} extra={<Tag>{cacheQ.data?.total ?? 0}</Tag>}>
        <Space wrap size={10}>
          {Object.entries(cacheQ.data?.by_role ?? {}).map(([role, n]) => (
            <Tag key={role}>
              {role}: {n}
            </Tag>
          ))}
          <Button size="small" icon={<ClearOutlined />} loading={clearQueryCache.isPending} onClick={() => clearQueryCache.mutate()}>
            {t.settings.cacheClearQuery}
          </Button>
          <Button size="small" danger loading={clearExtractCache.isPending} onClick={() => clearExtractCache.mutate('extract')}>
            {t.settings.cacheClearExtract}
          </Button>
        </Space>
      </Card>

      <Card size="small" title={t.settings.appearance}>
        <Space wrap size={20}>
          <Field label={t.settings.language}>
            <Select
              size="small"
              value={lang}
              onChange={(v) => setLang(v as Lang)}
              style={{ width: 140 }}
              options={[
                { value: 'zh', label: '中文' },
                { value: 'en', label: 'English' },
              ]}
            />
          </Field>
          <Field label={t.settings.ops}>
            <Button size="small" icon={<FileTextOutlined />} onClick={() => window.open('/docs', '_blank')}>
              {t.settings.apiDocs}
            </Button>
          </Field>
        </Space>
      </Card>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Space size={8}>
      <span style={{ color: 'var(--tf-ink-2)', minWidth: 120, display: 'inline-block' }}>{label}</span>
      {children}
    </Space>
  )
}
