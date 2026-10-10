"""变更精确检测（T5）：diff hunk 解析 / 函数 span 归属 / 索引器 end_line。"""

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8").stdout


def test_changed_lines_parses_hunks(tmp_path):
    """git diff -U0 → {file: [(start,end)]}：新增区间与纯删除 hunk 都要解析。"""
    from app.services.repo.gitops import changed_lines

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    f = repo / "mod.py"
    f.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    rev1 = _git(repo, "rev-parse", "HEAD").strip()
    # 改第 3 行 + 删第 5 行 + 追加第 6 行
    f.write_text("line1\nline2\nCHANGED\nline4\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "mod")
    rev2 = _git(repo, "rev-parse", "HEAD").strip()

    cmap = changed_lines(repo, rev1, rev2)
    assert "mod.py" in cmap
    ranges = cmap["mod.py"]
    assert any(s <= 3 <= e for s, e in ranges), "第 3 行修改必须命中"
    assert any(s <= 5 <= e for s, e in ranges), "第 5 行删除（纯删除 hunk）必须命中"


def test_changed_lines_noop():
    from app.services.repo.gitops import changed_lines

    assert changed_lines(Path("."), "", "abc") == {}
    assert changed_lines(Path("."), "abc", "abc") == {}


def test_fn_card_spans_and_end_line(tmp_path):
    """tree-sitter 卡片 end_line 覆盖函数体，spans 判定行区间归属。"""
    from app.services.repo.indexer import parse_file

    src = tmp_path / "svc.py"
    src.write_text(
        "def small():\n    return 1\n\n\ndef big():\n    x = 1\n    y = 2\n    z = 3\n    return x + y + z\n",
        encoding="utf-8",
    )
    cards = {c.name: c for c in parse_file(src, tmp_path)}
    assert cards["small"].line == 1 and cards["small"].end_line == 2
    assert cards["big"].line == 5 and cards["big"].end_line == 9
    assert cards["big"].spans(6, 7), "big 应覆盖第 6~7 行"
    assert not cards["small"].spans(6, 7), "small 不应误命中"
    # 纯删除 hunk（旧侧第 5~6 行）落在 big 内
    assert cards["big"].spans(5, 6)


def test_precise_attribution_ignores_line_shift():
    """只有行号平移（其他函数上方加行）时，未变更的函数不得被行级归因误标。"""
    from app.services.repo.indexer import parse_file

    src = tmp_path_py()
    cards = {c.name: c for c in parse_file(src, src.parent)}
    hunk = [(1, 2)]  # 变更只发生在文件头部 import 区
    hit = [c.name for c in cards.values() if any(c.spans(s, e) for s, e in hunk)]
    assert hit == [], "头部 import 变更不应归因到任何函数"


def tmp_path_py() -> Path:
    import tempfile

    d = Path(tempfile.mkdtemp())
    f = d / "svc.py"
    f.write_text(
        "import os\nimport sys\n\n\ndef target_fn():\n    return os.sep\n",
        encoding="utf-8",
    )
    return f
