"""LLM 错误类型：DeepSeek/智能体链路的统一异常。

系统无任何确定性假实现：Key 未配置、输出非法、schema 不符时一律显式报错。
"""


class LLMError(RuntimeError):
    pass
