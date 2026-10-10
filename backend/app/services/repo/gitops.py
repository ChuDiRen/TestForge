"""Git 操作（子命令方式）：clone / pull --ff-only / diff。

本地路径仓库用 git clone file:// 语义；凭据经 credential_ref 引用，不落明文。
"""

import logging
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from app.core.config import get_settings

log = logging.getLogger("repo-svc.git")


class GitError(RuntimeError):
    pass


_REMOTE_URL_RE = re.compile(r"^(https?://|git://|ssh://|git@[\w.-]+:)\S+", re.IGNORECASE)


def validate_remote_url(url: str, allow_local: bool = False) -> None:
    """仓库接入只认远程 Git URL；本地路径（file:// / 盘符 / 绝对相对路径）默认拒绝。

    allow_local 仅限本地开发/验收夹具场景（TF_ALLOW_LOCAL_REPO_URL=1）显式开启。
    """
    u = (url or "").strip()
    if allow_local:
        return
    lowered = u.lower()
    is_local = (
        lowered.startswith("file:")
        or lowered.startswith("\\\\")  # UNC
        or u.startswith(("/", "\\", "./", "../", "~"))
        or (len(u) >= 2 and u[1] == ":" and u[0].isalpha())  # Windows 盘符 C:\…
        or not _REMOTE_URL_RE.match(u)
    )
    if is_local:
        raise GitError("仅支持远程 Git URL（https://… 或 git@host:repo.git），不允许本地路径/file:// 上传")


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise GitError(f"{' '.join(cmd[:3])}... failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _rmtree_force(path: Path) -> None:
    """Windows 下 .git pack 文件带只读位，rmtree 会静默残留 → 清只读位后强删。"""

    def _onerror(func, p, _exc):  # type: ignore[no-untyped-def]
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_onerror)


def _is_valid_checkout(dest: Path) -> bool:
    """残留骨架（如只剩 .git/objects）不算有效检出，必须重克隆。"""
    git_dir = dest / ".git"
    return (git_dir / "HEAD").exists() and (git_dir / "config").exists()


def clone(url: str, dest: Path, branch: str, credential_ref: str = "") -> str:
    """克隆仓库到 dest（已存在有效检出则幂等返回 HEAD）。"""
    if _is_valid_checkout(dest):
        return _run(["git", "-C", str(dest), "rev-parse", "HEAD"]).strip()
    if dest.exists():
        _rmtree_force(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "50"]
    if branch and branch != "default":
        cmd += ["--branch", branch]
    cmd += [url, str(dest)]
    _run(cmd, timeout=300)
    return _run(["git", "-C", str(dest), "rev-parse", "HEAD"]).strip()


def pull(dest: Path) -> str:
    """pull --ff-only，返回 HEAD rev。"""
    if not dest.exists():
        raise GitError(f"local repo missing: {dest}")
    _run(["git", "-C", str(dest), "pull", "--ff-only"], timeout=120)
    return _run(["git", "-C", str(dest), "rev-parse", "HEAD"]).strip()


def changed_files(dest: Path, from_rev: str, to_rev: str) -> list[str]:
    if not from_rev or from_rev == to_rev:
        return []
    out = _run(["git", "-C", str(dest), "diff", "--name-only", from_rev, to_rev])
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def changed_lines(dest: Path, from_rev: str, to_rev: str) -> dict[str, list[tuple[int, int]]]:
    """行级变更图（GitNexus detect_changes 思路）：git diff -U0 → {file: [(start, end), ...]}。

    end 闭区间；纯删除 hunk 落在删除侧行号（用于判定被删函数所在区间）。
    解析失败返回空图（调用方回退文件级粒度）。
    """
    if not from_rev or from_rev == to_rev:
        return {}
    import re as _re

    out = _run(["git", "-C", str(dest), "diff", "-U0", "--no-color", from_rev, to_rev])
    result: dict[str, list[tuple[int, int]]] = {}
    cur_file = ""
    hunk_re = _re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    file_re = _re.compile(r"^\+\+\+ b/(.+)$")
    for ln in out.splitlines():
        m = file_re.match(ln)
        if m:
            cur_file = m.group(1).strip()
            continue
        m = hunk_re.match(ln)
        if m and cur_file:
            old_start, old_count, new_start, new_count = (int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1))
            if new_count > 0:
                result.setdefault(cur_file, []).append((new_start, new_start + new_count - 1))
            else:
                # 纯删除 hunk：新侧无行，用旧侧被删行号定位（用于判定被删函数区间）
                result.setdefault(cur_file, []).append((old_start, old_start + max(1, old_count) - 1))
    return result


def rev_of(dest: Path, ref: str = "HEAD") -> str:
    return _run(["git", "-C", str(dest), "rev-parse", ref]).strip()


def prev_rev(dest: Path) -> str:
    """HEAD~1（增量演示用；无历史返回空）。"""
    proc = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD~1"], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def repo_local_path(url_or_path: str) -> Path:
    """统一克隆到 data/repos/<name>：源可为本地路径/file://，检出与源分离保证 pull --ff-only 语义。"""
    s = url_or_path
    if s.startswith("file://"):
        s = s[7:]
    settings = get_settings()
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", s.rstrip("/\\").split("/")[-1].removesuffix(".git")) or "repo"
    return Path(settings.repo_root) / name
