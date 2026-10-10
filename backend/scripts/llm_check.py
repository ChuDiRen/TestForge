"""DeepSeek 连通性自检：真实调用 /chat/completions（json_object 模式），验证 key/model/base_url。

用法：make llm-check   （或 uv run python scripts/llm_check.py）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    key = s.llm_api_key.strip()
    print(f"base_url = {s.llm_base_url}")
    print(f"model    = {s.llm_model}")
    print(f"key      = {'已填（{}…）'.format(key[:6]) if key else '未填'}")
    if not key:
        print("\n[FAIL] LLM_API_KEY 为空：到 https://platform.deepseek.com 申请 key 填入 .env 后重试")
        return 1

    resp = httpx.post(
        f"{s.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": s.llm_model,
            "messages": [
                {"role": "system", "content": "只输出符合要求的 JSON，不要多余文本。"},
                {"role": "user", "content": '返回 JSON：{"ok": true, "model": "<你的模型名>"}'},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        print(f"\n[FAIL] HTTP {resp.status_code}: {resp.text[:200]}")
        return 1
    content = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    print(f"\n[OK] DeepSeek 握手成功：{data}")
    print("管线中的通用函数规划已走 DeepSeek；精选/探针靶标保持确定性策略。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
