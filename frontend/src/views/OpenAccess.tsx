import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Button, Card, message, Table, Tag } from "antd";
import { CopyOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";

const MCP_CONFIG = `{
  "mcpServers": {
    "testforge": {
      "command": "uv",
      "args": ["run", "--project", "E:/TestForge/backend", "python", "-m", "services.mcp_server"]
    }
  }
}`;

const TOOLS: [string, string][] = [
  ["list_functions", "列出仓库已索引函数（模块/签名/文件行号）"],
  ["function_impact", "函数影响半径：受变更影响的调用方（BFS 分层）"],
  ["trace_path", "两个符号之间的最短调用路径"],
  ["detect_changes", "git 工作区未提交变更 → 受影响函数与风险分"],
  ["call_cycles", "调用环检测（循环依赖）"],
  ["entry_chains", "入口最长调用链"],
  ["wiki_ask", "Wiki 知识库问答（混合检索后作答，带来源）"],
  ["wiki_lint", "Wiki 健康体检（stale/重复/孤儿页）"],
  ["search_cases", "用例库混合检索（向量+全文 RRF 融合）"],
  ["get_symbol_context", "符号 360° 视图：签名+调用方+被调+用例覆盖"],
  ["knowledge_query", "文档知识图谱双层检索（LightRAG 模式）"],
  ["repo_status", "仓库接入状态与统计"],
];

/** 开放接入：外部 AI（Cursor / Claude / 任意 MCP 客户端）消费 TestForge 知识层。 */
export function OpenAccess() {
  const [copied, setCopied] = useState(false);
  const qc = useQueryClient();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(MCP_CONFIG);
      setCopied(true);
      message.success("已复制 MCP 配置");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      message.error("复制失败——请手动选择文本复制");
    }
  };

  // 一键接入 Claude Desktop（OpenWiki commands/mcp.rs 移植）：备份后合并注入 mcpServers
  const statusQ = useQuery({
    queryKey: ["mcp-claude-status"],
    queryFn: () => get<{ config_path: string; exists: boolean; injected: boolean }>("/api/mcp/claude-desktop/status"),
  });
  const install = useMutation({
    mutationFn: () => post<{ config_path: string }>("/api/mcp/claude-desktop/install"),
    onSuccess: (r) => {
      message.success(`已注入 ${r.config_path}——重启 Claude Desktop 生效`);
      qc.invalidateQueries({ queryKey: ["mcp-claude-status"] });
    },
    onError: (e: any) => message.error(e.message),
  });

  return (
    <div>
      <PageHeader title="开放接入" subtitle="把 TestForge 的代码图谱 / Wiki / 用例库通过 MCP 协议开放给外部 AI 编程工具" />
      <Card title="MCP 接入（Cursor / Claude Desktop / 任意 MCP 客户端）" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 13, color: "var(--tf-ink-2)", marginTop: 0 }}>
          TestForge 内置 stdio MCP 服务器（12 个只读工具）。在 MCP 客户端的配置文件中加入以下片段：
        </p>
        <div
          style={{
            position: "relative",
            background: "var(--tf-bg)",
            border: "1px solid var(--tf-line)",
            borderRadius: 8,
            padding: "12px 44px 12px 14px",
            fontFamily: "Consolas, monospace",
            fontSize: 12.5,
            whiteSpace: "pre",
            overflowX: "auto",
          }}
        >
          {MCP_CONFIG}
          <Button
            size="small"
            type="text"
            icon={<CopyOutlined />}
            style={{ position: "absolute", top: 8, right: 8 }}
            onClick={copy}
          />
        </div>
        {copied && <Tag color="green" style={{ marginTop: 8 }}>已复制到剪贴板</Tag>}
        <p style={{ fontSize: 13, color: "var(--tf-ink-2)" }}>
          配置后外部 AI 即可查询代码图谱、调用链、影响半径、Wiki 问答与用例库——改代码前先问影响面，
          与 GitNexus 的「Precomputed Relational Intelligence」同一工作流。
        </p>
        {/* 一键接入（OpenWiki commands/mcp.rs 移植）：探测 Claude Desktop 配置 → 备份 → 合并注入 */}
        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: "1px dashed var(--tf-line)",
            display: "flex",
            alignItems: "center",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          <Button
            type="primary"
            ghost
            icon={<ThunderboltOutlined />}
            loading={install.isPending}
            onClick={() => install.mutate()}
          >
            一键接入 Claude Desktop
          </Button>
          {statusQ.data && (
            <span style={{ fontSize: 12, color: "var(--tf-ink-3)" }}>
              {!statusQ.data.exists
                ? "未检测到 Claude Desktop 配置（安装后可一键注入）"
                : `${statusQ.data.injected ? "✅ 已注入 TestForge MCP" : "检测到配置文件，尚未接入"}（${statusQ.data.config_path}）`}
            </span>
          )}
        </div>
      </Card>
      <Card title={`开放工具（${TOOLS.length} 个只读）`}>
        <Table
          rowKey={(r) => r[0]}
          size="small"
          pagination={false}
          dataSource={TOOLS}
          columns={[
            { title: "工具", dataIndex: 0, width: 200, render: (v: string) => <span style={{ fontFamily: "Consolas, monospace", fontSize: 12.5 }}>{v}</span> },
            { title: "能力", dataIndex: 1 },
          ]}
        />
      </Card>
    </div>
  );
}
