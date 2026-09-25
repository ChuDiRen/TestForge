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

from services.shared.config import get_settings

log = logging.getLogger("repo-svc.git")


class GitError(RuntimeError):
    pass


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
