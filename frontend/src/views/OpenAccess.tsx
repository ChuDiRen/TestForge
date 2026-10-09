import { PageHeader } from "../components/PageHeader";
import { useState } from "react";
import { Button, Card, message, Table, Tag } from "antd";
import { CopyOutlined } from "@ant-design/icons";

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
  ["wiki_ask", "Wiki 知识库问答（检索后作答，带来源）"],
  ["wiki_lint", "Wiki 健康体检（stale/重复/孤儿页）"],
  ["search_cases", "用例库检索（分层/类别/模块过滤）"],
  ["get_symbol_context", "符号 360° 视图：签名+调用方+被调+用例覆盖"],
  ["knowledge_query", "文档知识图谱双层检索（LightRAG 模式）"],
  ["repo_status", "仓库接入状态与统计"],
];

/** 开放接入：外部 AI（Cursor / Claude / 任意 MCP 客户端）消费 TestForge 知识层。 */
export function OpenAccess() {
  const [copied, setCopied] = useState(false);
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
