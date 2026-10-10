"""变更影响检测（GitNexus detect_changes 的移植）：

git diff（未暂存/已暂存/对比基线）→ 映射到已索引函数 → 直接调用方与影响半径，
产出"这次改动会波及什么"的报告。供图谱高亮、仓库接入页与 MCP/助手工具使用。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.db.session import get_session
from app.models import CallEdges, FnImpact, Functions, Repos

_SCOPES = ("unstaged", "staged", "all")


def _git_files(local: Path, args: list[str]) -> list[str]:
    out = subprocess.run(
        ["git", *args],
        cwd=local,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if out.returncode != 0:
        return []
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def changed_files(repo: Repos, scope: str = "unstaged", base_ref: str = "") -> list[str]:
    """按 scope 取变更文件列表（仓库根相对路径）。"""
    local = Path(repo.local_path or "")
    if not local.is_dir():
        raise FileNotFoundError(f"仓库检出目录不存在：{local}（重新 pull 一次可修复）")
    if base_ref:
        args = ["diff", "--name-only", base_ref]
    elif scope == "staged":
        args = ["diff", "--name-only", "--cached"]
    elif scope == "all":
        args = ["diff", "--name-only", "HEAD"]
    else:
        args = ["diff", "--name-only"]
    return _git_files(local, args)


def _norm(p: str) -> str:
    return p.replace("\\", "/").lstrip("./")


def detect_changes(repo_id: int, scope: str = "unstaged", base_ref: str = "") -> dict:
    with get_session() as sess:
        repo = sess.get(Repos, repo_id)
        if repo is None:
            raise LookupError("仓库不存在")
        files = changed_files(repo, scope, base_ref)
        fns = sess.query(Functions.id, Functions.name, Functions.module, Functions.file).filter(
            Functions.repo_id == repo_id
        ).all()
        impact = {r.fn_name: r for r in sess.query(FnImpact).filter(FnImpact.repo_id == repo_id).all()}
        id_name = {i: n for i, n, _, _ in fns}
        callers: dict[int, set[int]] = {}
        if fns:
            for e in sess.query(CallEdges).filter(CallEdges.callee_id.in_(list(id_name))).all():
                callers.setdefault(e.callee_id, set()).add(e.caller_id)

    norm_files = {_norm(f) for f in files}
    hit: list[dict] = []
    hit_files: set[str] = set()
    for fid, name, module, file in fns:
        nf = _norm(file or "")
        if not nf:
            continue
        matched = next((cf for cf in norm_files if nf.endswith(cf) or cf.endswith(nf)), None)
        if matched is None:
            continue
        hit_files.add(matched)
        direct = sorted(id_name[c] for c in callers.get(fid, ()) if c in id_name)
        imp = impact.get(name)
        hit.append(
            {
                "name": name,
                "module": module,
                "file": file,
                "direct_callers": direct[:12],
                "callers_count": len(direct),
                "reach_count": imp.reach_count if imp else len(direct),
                "risk": round(float(imp.score), 2) if imp else round(min(1.0, len(direct) / 10), 2),
            }
        )

    hit.sort(key=lambda x: (-x["reach_count"], -x["callers_count"]))
    return {
        "scope": base_ref or scope,
        "changed_files": len(norm_files),
        "affected_functions": len(hit),
        "functions": hit[:80],
        "unmapped_files": sorted(norm_files - hit_files)[:30],
    }
