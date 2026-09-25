"""测试全局配置：所有测试跑在独立的 testforge_test 库，绝不污染开发数据。"""

import os

# 必须在任何 services 导入前生效（pydantic-settings 优先读进程 env）
os.environ["DATABASE_URL"] = "postgresql+psycopg://admin:testforge@192.168.10.5:5432/testforge_test"
