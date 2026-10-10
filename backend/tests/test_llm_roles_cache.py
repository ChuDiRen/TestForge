"""LLM 角色路由（T2）与抽取缓存（T3）：角色→模型解析、缓存命中不重付 token。"""


def test_model_for_role_fallback_and_override(monkeypatch):
    from app.core import config as cfg
    from app.services.knowledge.llm import LLMError, model_for

    class S:
        llm_model = "deepseek-chat"
        llm_model_extract = ""
        llm_model_query = "deepseek-reasoner"
        llm_model_keyword = ""

    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    assert model_for("extract") == "deepseek-chat", "extract 未配置应回退主模型"
    assert model_for("query") == "deepseek-reasoner", "query 应取角色专属强模型"
    assert model_for("keyword") == "deepseek-chat"
    try:
        model_for("bogus")
        raise AssertionError("未知角色必须显式报错")
    except LLMError:
        pass


def test_chat_cached_hit_skips_llm(monkeypatch):
    """同一 (role, system, prompt) 第二次调用不得再触发 LLM（增量重建零成本）。"""
    from app.db.session import init_db
    from app.services.knowledge import llm as llm_mod
    from app.services.knowledge.llm_cache import cache_key, cache_stats, chat_cached, clear_cache

    init_db()
    calls = {"n": 0}

    def fake_chat(prompt, system="", role="extract"):
        calls["n"] += 1
        return f"resp-{calls['n']}"

    monkeypatch.setattr(llm_mod, "chat_once", fake_chat)
    key = cache_key("extract", "", "hello cache")
    clear_cache()  # 全清，保证从 miss 开始
    try:
        assert chat_cached("hello cache", role="extract") == "resp-1"
        assert chat_cached("hello cache", role="extract") == "resp-1", "命中缓存应返回同一结果"
        assert calls["n"] == 1, "第二次调用不应再打 LLM"
        stats = cache_stats()
        assert stats["total"] >= 1
        # 不同角色同一文本是不同缓存键
        assert chat_cached("hello cache", role="query") == "resp-2"
        assert calls["n"] == 2
    finally:
        with __import__("app.db.session", fromlist=["get_session"]).get_session() as sess:
            from app.models import LlmCache

            sess.query(LlmCache).filter(LlmCache.cache_key.in_([key, cache_key("query", "", "hello cache")])).delete(synchronize_session=False)
            sess.commit()


def test_chat_once_raises_without_key(monkeypatch):
    from app.core import config as cfg
    from app.services.knowledge.llm import LLMError, chat_once

    class S:
        llm_api_key = ""
        llm_model = "deepseek-chat"
        llm_base_url = "https://api.deepseek.com"
        llm_model_extract = ""
        llm_model_query = ""
        llm_model_keyword = ""

    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    try:
        chat_once("x")
        raise AssertionError("无 Key 必须显式报错（无 mock 原则）")
    except LLMError:
        pass
