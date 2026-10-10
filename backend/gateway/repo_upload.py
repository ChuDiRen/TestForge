"""仓库接入扩展（入口①）：上传代码包 zip → 安全解压 → 知识图谱流水线。

安全栏（GitNexus 摄入通道设计的 zip 版移植，只借思想）：
- 仅 admin；仅 .zip；单包 ≤200MB；成员 ≤20000；解压总量 ≤1GB
- zip-slip 防护：成员路径拒绝绝对路径 / .. / 盘符 / 反斜杠穿越
- 排除目录清单（.git/node_modules/__pycache__/venv/构建产物…）枚举期即剪枝
- 解压目标困定在 data/repos/upload-<slug>，slug 白名单字符消毒
同名重传 = 更新图谱（upload:// 无远端，重传走全量重建）。
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Request

from gateway.envelope import ApiError, ok

router = APIRouter()

MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024

# 枚举期剪枝：依赖/构建产物/IDE 目录不进索引（与 GitNexus upload-filter 同思路）
EXCLUDED_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode",
    ".next", "dist", "build", "target", "out", ".gradle", ".mvn", "vendor", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "coverage", ".terraform",
}

_SLUG_RE = re.compile(r"[^0-9A-Za-z._-]+")


def sanitize_slug(name: str) -> str:
    """仓库名消毒（GitNexus repo-name 正则同思路）：白名单字符，其余替换为 -。"""
    slug = _SLUG_RE.sub("-", (name or "").strip()).strip("-.")
    return slug[:64] or "uploaded-repo"


def _member_rel_path(name: str) -> PurePosixPath:
    """校验单个 zip 成员路径，返回净化后的相对路径；非法即 422（zip-slip 防护）。"""
    if "\\" in name:
        raise ApiError(422, f"压缩包内含非法路径分隔符: {name}", 422)
    p = PurePosixPath(name)
    if p.is_absolute() or name.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", name):
        raise ApiError(422, f"压缩包内含绝对路径: {name}", 422)
    parts = [seg for seg in p.parts if seg not in (".",)]
    if any(seg == ".." for seg in parts):
        raise ApiError(422, f"压缩包内含路径穿越（..）: {name}", 422)
    if not parts:
        raise ApiError(422, f"压缩包内含空路径成员: {name!r}", 422)
    return PurePosixPath(*parts)


def plan_members(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """枚举并校验全部成员：路径合法 + 排除目录剪枝 + 数量/总量上限。返回待解压清单。"""
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ApiError(422, f"压缩包成员数 {len(infos)} 超过上限 {MAX_MEMBERS}", 422)
    total = 0
    planned: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info in infos:
        rel = _member_rel_path(info.filename)
        if any(seg in EXCLUDED_DIRS for seg in rel.parts):
            continue
        if info.is_dir():
            continue
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise ApiError(422, f"解压后总大小超过上限 {MAX_TOTAL_UNCOMPRESSED // (1024 * 1024)}MB", 422)
        planned.append((info, rel))
    if not planned:
        raise ApiError(422, "压缩包内没有可索引的文件（或全部被排除目录过滤）", 422)
    return planned


def _strip_single_root(planned: list[tuple[zipfile.ZipInfo, PurePosixPath]]) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """GitHub 式整包根目录（repo-main/...）剥壳：唯一顶层目录且无顶层散文件时下钻一层。"""
    tops = {rel.parts[0] for _, rel in planned}
    if len(tops) == 1 and all(len(rel.parts) > 1 for _, rel in planned):
        planned = [(info, PurePosixPath(*rel.parts[1:])) for info, rel in planned]
    return planned


@router.post("/api/repos/upload")
async def upload_repo_zip(request: Request):
    """上传代码包（multipart zip）→ 安全解压 → RegisterUpload 全量流水线（同步返回 steps）。"""
    from gateway.auth_routes import _require_admin
    from gateway.main import GRPC_PORTS, grpc_call

    _require_admin(request)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise ApiError(400, "multipart 字段 file 必填（.zip 代码包）", 400)
    filename = str(getattr(upload, "filename", "repo.zip"))
    if not filename.lower().endswith(".zip"):
        raise ApiError(422, "仅支持 .zip 代码包", 422)
    data = await upload.read()
    if not data:
        raise ApiError(400, "上传文件为空", 400)
    if len(data) > MAX_ZIP_BYTES:
        raise ApiError(422, f"压缩包超过大小上限 {MAX_ZIP_BYTES // (1024 * 1024)}MB", 422)
    name = str(form.get("name") or "").strip() or Path(filename).stem
    slug = sanitize_slug(name)

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ApiError(422, "不是有效的 zip 压缩包", 422) from exc
    from services.shared.config import get_settings

    with zf:
        planned = _strip_single_root(plan_members(zf))
        dest_root = Path(get_settings().repo_root) / f"upload-{slug}"
        if dest_root.exists():
            shutil.rmtree(dest_root, ignore_errors=True)
        dest_root.mkdir(parents=True, exist_ok=True)
        for info, rel in planned:
            target = (dest_root / Path(*rel.parts)).resolve()
            if dest_root.resolve() not in target.parents:
                raise ApiError(422, f"解压目标越界: {rel}", 422)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))

    res = grpc_call(
        "repo-svc",
        GRPC_PORTS["repo-svc"],
        "RepoSvc",
        "RegisterUpload",
        {"name": slug, "path": str(dest_root), "branch": str(form.get("branch") or "main")},
        timeout=600,
    )
    repo = res.get("repo") or {}
    return ok(
        {
            "id": repo.get("id"),
            "name": slug,
            "url": repo.get("url"),
            "status": repo.get("status"),
            "functions": int(res.get("functions") or 0),
            "call_edges": int(res.get("call_edges") or 0),
            "wiki_pages": int(res.get("wiki_pages") or 0),
            "steps": res.get("steps") or [],
        }
    )
