import { PageHeader } from "../components/PageHeader";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Card, Empty, Input, Select, Space, Spin, Switch, Tabs, Tag, Tooltip, Typography } from "antd";
import { CaretRightOutlined, PauseOutlined } from "@ant-design/icons";
import { get, postStreamSSE } from "../api";
import { Markdown } from "../components/Markdown";
import { useLang } from "../i18n";

interface Hit {
  name: string;
  content: string;
  score: number;
  doc_key: string;
  meta: Record<string, unknown>;
}
interface RefItem {
  ref: string;
  via: string;
  title?: string;
  snippet?: string;
}
interface RetrievedEv {
  type: "retrieved";
  payload: {
    mode: string;
    keywords: { high_level: string; low_level: string };
    entities: Hit[];
    relations: Hit[];
    communities: Hit[];
    chunks: Hit[];
    references: RefItem[];
    web_results: { title: string; snippet: string; url: string }[];
    tokens_used: Record<string, number>;
    timings: Record<string, number>;
  };
}
interface DoneEv {
  type: "done";
  payload: RetrievedEv["payload"] & { answer: string; cached: boolean };
}

const MODES = ["naive", "local", "global", "hybrid", "mix"] as const;

/** 检索实验室（LightRAG WebUI Retrieval 对齐）：六模式对照 + 流式回答 + contexts/引用面板。 */
export function RetrievalLab({ embedded = false }: { embedded?: boolean } = {}) {
  const { t, lang } = useLang();
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<(typeof MODES)[number]>("mix");
  const [topK, setTopK] = useState(8);
  const [ws, setWs] = useState("");
  const [websearch, setWebsearch] = useState(false);
  const [bypass, setBypass] = useState(false);
  const [answer, setAnswer] = useState("");
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState<DoneEv["payload"] | null>(null);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const wsQ = useQuery({ queryKey: ["kg-workspaces"], queryFn: () => get<string[]>("/api/kg/workspaces"), refetchOnMount: false });

  const run = async () => {
    if (!query.trim() || running) return;
    setRunning(true);
    setAnswer("");
    setNotice("");
    setResult(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await postStreamSSE(
        "/api/kg/query/stream",
        { query, mode, top_k: topK, workspace: ws, websearch, use_cache: !bypass },
        (raw) => {
          const ev = raw as { type: string; payload?: DoneEv["payload"]; delta?: string; message?: string; cached?: boolean };
          if (ev.type === "retrieved" || ev.type === "done") {
            if (ev.payload) setResult(ev.payload);
          } else if (ev.type === "delta") setAnswer((a) => a + (ev.delta ?? ""));
          else if (ev.type === "notice") setNotice(ev.message ?? "");
        },
        ctrl.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") setNotice((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const kw = result?.keywords;

  return (
    <div>
      {!embedded && <PageHeader title={t.lab.title} subtitle={t.lab.subtitle} />}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              size="large"
              placeholder={t.lab.queryPh}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onPressEnter={run}
              disabled={running}
            />
            {running ? (
              <Button size="large" danger icon={<PauseOutlined />} onClick={() => abortRef.current?.abort()}>
                {t.lab.stop}
              </Button>
            ) : (
              <Button size="large" type="primary" icon={<CaretRightOutlined />} onClick={run} disabled={!query.trim()}>
                {t.lab.run}
              </Button>
            )}
          </Space.Compact>
          <Space wrap size={14}>
            <Tooltip title={t.lab.modeDesc[mode]}>
              <Space size={6}>
                <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>{t.lab.mode}</span>
                <Select value={mode} onChange={setMode} size="small" style={{ width: 110 }}
                  options={MODES.map((m) => ({ value: m, label: <Tag color={m === "mix" ? "blue" : "default"}>{m}</Tag> }))} />
              </Space>
            </Tooltip>
            <Space size={6}>
              <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>{t.lab.topk}</span>
              <Select value={topK} onChange={setTopK} size="small" style={{ width: 70 }}
                options={[4, 6, 8, 12, 20].map((n) => ({ value: n, label: n }))} />
            </Space>
            <Select value={ws || undefined} onChange={(v) => setWs(v || "")} size="small" style={{ width: 150 }} allowClear
              placeholder="workspace（全部）"
              options={[...new Set(["default", ...(wsQ.data ?? [])])].map((w) => ({ value: w, label: w }))} />
            <span><Switch size="small" checked={websearch} onChange={setWebsearch} /> <span style={{ fontSize: 12 }}>{t.lab.websearch}</span></span>
            <span><Switch size="small" checked={bypass} onChange={setBypass} /> <span style={{ fontSize: 12 }}>{t.lab.bypassCache}</span></span>
          </Space>
        </Space>
      </Card>

      {(answer || notice || result) && (
        <Card size="small" style={{ marginBottom: 16 }} title={t.lab.answer} extra={result?.cached ? <Tag color="cyan">{t.lab.cached}</Tag> : undefined}>
          {notice && <div style={{ marginBottom: 8, fontSize: 12.5, color: "#d48806" }}>⚠ {notice}</div>}
          {running && !answer ? <Spin size="small" /> : answer ? <Markdown>{answer}</Markdown> : <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>{t.lab.answerHint}</Typography.Text>}
        </Card>
      )}

      {!answer && !result && !running && (
        <Card size="small"><Empty description={t.lab.empty} image={Empty.PRESENTED_IMAGE_SIMPLE} /></Card>
      )}

      {result && (
        <Tabs
          size="small"
          items={[
            kw && {
              key: "kw",
              label: `🔎 Keywords`,
              children: (
                <div style={{ display: "grid", gap: 8, fontSize: 13 }}>
                  <div><Tag color="purple">{t.lab.keywordsHl}</Tag> {kw.high_level}</div>
                  <div><Tag color="geekblue">{t.lab.keywordsLl}</Tag> {kw.low_level}</div>
                  <div style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>
                    token 预算消耗：{Object.entries(result.tokens_used).map(([k, v]) => `${k}=${v}`).join(" · ")} | {result.timings?.total_ms ?? 0}ms
                  </div>
                </div>
              ),
            },
            {
              key: "entities",
              label: `${t.lab.entities} (${result.entities.length})`,
              children: <HitList hits={result.entities} />,
            },
            {
              key: "relations",
              label: `${t.lab.relations} (${result.relations.length})`,
              children: <HitList hits={result.relations} />,
            },
            ...(result.communities.length ? [{ key: "communities", label: `${t.lab.communities} (${result.communities.length})`, children: <HitList hits={result.communities} /> }] : []),
            {
              key: "chunks",
              label: `${t.lab.chunks} (${result.chunks.length})`,
              children: <HitList hits={result.chunks} />,
            },
            {
              key: "refs",
              label: `${t.lab.references} (${result.references.length})`,
              children: (
                <div style={{ display: "grid", gap: 6 }}>
                  {result.references.map((r) => (
                    <div key={r.ref} style={{ fontSize: 12.5 }}>
                      <Tag color={r.via === "websearch" ? "orange" : r.via === "chunk" ? "cyan" : "default"}>{r.via}</Tag>
                      <code style={{ fontSize: 12 }}>{r.title || r.ref}</code>
                      {r.snippet && <div style={{ color: "var(--tf-ink-3)", fontSize: 12, marginLeft: 8 }}>{r.snippet.slice(0, 120)}</div>}
                    </div>
                  ))}
                  {!result.references.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
                </div>
              ),
            },
          ].filter(Boolean) as never[]}
        />
      )}
    </div>
  );
}

function HitList({ hits }: { hits: Hit[] }) {
  if (!hits.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {hits.map((h) => (
        <Card key={h.doc_key} size="small" title={<span style={{ fontSize: 13 }}>{h.name}</span>} extra={<span style={{ fontFamily: "Consolas, monospace", fontSize: 11.5 }}>{h.score.toFixed(4)}</span>}>
          <div style={{ fontSize: 12.5, color: "var(--tf-ink-2)", whiteSpace: "pre-wrap" }}>{h.content}</div>
        </Card>
      ))}
    </div>
  );
}
