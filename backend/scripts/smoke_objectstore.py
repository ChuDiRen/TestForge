"""对象存储冒烟：对真实 MinIO 验证字节往返 + 目录快照/还原 + 三桶键布局。

用法：uv run python scripts/smoke_objectstore.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.objectstore import content_digest, get_objectstore, safe_key_part  # noqa: E402


def main() -> int:
    cfg = get_settings()
    store = get_objectstore()
    print(f"[smoke] endpoint={cfg.s3_endpoint or '(未配置→功能关闭)'}")
    if not store.enabled:
        print("[smoke] S3_ENDPOINT 未配置，冒烟无意义")
        return 1

    # ① 字节往返
    key = f"smoke/{content_digest(b'hello-testforge')}/hello.txt"
    saved = store.put_bytes(cfg.s3_bucket_artifacts, key, b"hello-testforge")
    back = store.get_bytes(cfg.s3_bucket_artifacts, saved or key)
    print(f"[smoke] ① 字节往返: put={bool(saved)} get_match={back == b'hello-testforge'}")

    # ② 目录快照/还原（模拟 repo 检出缓存丢失自愈）
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "checkout"
        (src / "pkg" / "__pycache__").mkdir(parents=True)
        (src / "pkg" / "mod.py").write_text("def fn():\n    return 42\n", encoding="utf-8")
        (src / "README.md").write_text("# demo\n", encoding="utf-8")
        (src / "pkg" / "__pycache__" / "junk.pyc").write_bytes(b"\x00junk")
        snap_key = store.snapshot_dir(src, cfg.s3_bucket_repos, "smoke/checkout/snapshot.tar.gz")
        dst = Path(td) / "restored"
        restored = store.restore_dir(dst, cfg.s3_bucket_repos, snap_key or "smoke/checkout/snapshot.tar.gz")
        content = (dst / "pkg" / "mod.py").read_text(encoding="utf-8") if restored else ""
        no_junk = restored and not (dst / "pkg" / "__pycache__").exists()
        print(f"[smoke] ② 目录快照: snapshot={bool(snap_key)} restored={restored} content_ok={'return 42' in content} pycache_excluded={no_junk}")

    # ③ 三桶键布局巡检
    for bucket, prefix in (
        (cfg.s3_bucket_repos, "repos/"),
        (cfg.s3_bucket_assets, "assets/"),
        (cfg.s3_bucket_artifacts, "smoke/"),
    ):
        keys = store.list_prefix(bucket, prefix)
        print(f"[smoke] ③ {bucket}: {len(keys)} 个对象")
        for k in keys[:3]:
            print(f"        - {k}")

    print(f"[smoke] 键消毒样例: {safe_key_part('测试 用例/报告 v1.pdf')} → assets 键安全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
