import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 全站 Markdown 渲染组件（GFM：表格/任务列表/删除线/自动链接），统一排版 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
