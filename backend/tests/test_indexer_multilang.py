"""多语言 tree-sitter 索引单测（go / java / javascript / python）。"""

from pathlib import Path

from app.services.repo.indexer import index_repo, is_supported_file, lang_of

SAMPLES = {
    "calc.go": '''package calc

// Add 两数相加。
func Add(a int, b int) int {
	return helper(a, b)
}

func helper(a int, b int) int {
	return a + b
}
''',
    "src/main/java/com/demo/Calc.java": '''package com.demo;

public class Calc {
    /** 两数相加。 */
    public int add(int a, int b) {
        return helper(a, b);
    }

    private int helper(int a, int b) {
        return a + b;
    }
}
''',
    "src/util.js": '''// 相加工具
function add(a, b) {
  return helper(a, b);
}

function helper(a, b) {
  return a + b;
}
''',
    "app.py": '''def add(a, b):
    """两数相加。"""
    return helper(a, b)

def helper(a, b):
    return a + b
''',
}


def _make_repo(tmp_path: Path) -> Path:
    for rel, content in SAMPLES.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return tmp_path


def test_lang_detection():
    assert lang_of("a/b/main.go") == "go"
    assert lang_of("x.Component.tsx") == "tsx"
    assert lang_of("README.md") is None
    assert is_supported_file("a.rs") and not is_supported_file("a.txt")


def test_multilang_functions_indexed(tmp_path):
    root = _make_repo(tmp_path)
    cards = index_repo(root)
    by_lang = {}
    for c in cards:
        by_lang.setdefault(c.language, set()).add(c.name)
    # 每种语言都索引到 add（方法/函数；Go 导出函数首字母大写）
    for lang in ("go", "java", "javascript", "python"):
        assert lang in by_lang, f"{lang} 未索引: {by_lang}"
        assert any(n.lower().endswith("add") for n in by_lang[lang]), f"{lang} 缺 add 函数: {by_lang[lang]}"


def test_calls_captured_across_languages(tmp_path):
    root = _make_repo(tmp_path)
    cards = index_repo(root)
    for c in cards:
        if c.name.endswith("add"):
            assert "helper" in c.calls, f"{c.language} add 未捕获 helper 调用: {c.calls}"


def test_java_method_class_prefix(tmp_path):
    root = _make_repo(tmp_path)
    cards = index_repo(root)
    java_names = {c.name for c in cards if c.language == "java"}
    assert "Calc.add" in java_names and "Calc.helper" in java_names
