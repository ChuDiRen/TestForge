import { Alert, Button, Space } from "antd";

export interface NextStepAction {
  label: string;
  view: string;
}

/**
 * 流程引导：当前操作完成后提示下一步去哪（点击经 tf-navigate 切页，不刷新）。
 * 用在「仓库接入成功后」「需求生效后」等动线断裂点。
 */
export function NextStep({ title, actions }: { title: string; actions: NextStepAction[] }) {
  return (
    <Alert
      type="success"
      showIcon
      message={title}
      style={{ marginBlockEnd: 12 }}
      action={
        <Space wrap>
          {actions.map((a) => (
            <Button
              key={a.view}
              size="small"
              type="primary"
              ghost
              onClick={() => window.dispatchEvent(new CustomEvent("tf-navigate", { detail: a.view }))}
            >
              {a.label}
            </Button>
          ))}
        </Space>
      }
    />
  );
}
