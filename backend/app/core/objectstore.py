"""对象存储（S3/MinIO）客户端：持久层真源，本地磁盘只是工作缓存。

三类数据分桶隔离（桶内键再按业务对象前缀隔离）：
- repos 桶      repos/{repo_id}/{rev[:12]}/snapshot.tar.gz        拉取的源码快照
- assets 桶     assets/{scope}/{kind}/{sha256[:16]}/{filename}     上传的原始文件（pdf/docx/xlsx 原件）
- artifacts 桶  cases/{case_code}/script.py、runs/{run_code}/...   生成脚本与执行产物

语义约定：
- s3_endpoint 未配置 = 功能整体关闭，所有方法静默返回空值，调用方回退纯本地行为；
- 所有写入 best-effort：MinIO 不可达只告警，绝不阻断接入/上传/执行主流程。
"""

from __future__ import annotations

import functools
import hashlib
import io
import logging
import re
import shutil
import tarfile
from pathlib import Path

from app.core.config import get_settings

log = logging.getLogger("objectstore")

_UNSAFE_KEY_CHARS = re.compile(r"[^\w.\-@+]")


def safe_key_part(text: str) -> str:
    """键片段消毒：中文/空格/路径分隔符统一压成安全字符，避免对象键歧义。"""
    part = _UNSAFE_KEY_CHARS.sub("_", text.strip())
    return part[:120] or "unnamed"


def content_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


class ObjectStore:
    """boto3 薄封装：懒建客户端 + 桶自动创建 + 目录快照/还原。"""

    def __init__(self) -> None:
        self._client = None
        self._ready_buckets: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(get_settings().s3_endpoint)

    def _s3(self):
        if self._client is not None:
            return self._client
        cfg = get_settings()
        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint,
            aws_access_key_id=cfg.s3_access_key or None,
            aws_secret_access_key=cfg.s3_secret_key or None,
            region_name="us-east-1",
            config=Config(signature_version="s3v4", retries={"max_attempts": 2, "mode": "standard"}),
        )
        return self._client

    def ensure_bucket(self, bucket: str) -> bool:
        """桶不存在则创建（幂等）；返回桶当前是否可用。"""
        if not self.enabled or bucket in self._ready_buckets:
            return bool(self.enabled)
        try:
            s3 = self._s3()
            buckets = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
            if bucket not in buckets:
                s3.create_bucket(Bucket=bucket)
            self._ready_buckets.add(bucket)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("MinIO 桶 %s 不可用：%s", bucket, exc)
            return False

    def put_bytes(self, bucket: str, key: str, data: bytes) -> str:
        """写入对象，成功返回键，失败返回空串（best-effort）。"""
        if not self.ensure_bucket(bucket):
            return ""
        try:
            self._s3().put_object(Bucket=bucket, Key=key, Body=data)
            return key
        except Exception as exc:  # noqa: BLE001
            log.warning("MinIO put %s/%s 失败：%s", bucket, key, exc)
            return ""

    def get_bytes(self, bucket: str, key: str) -> bytes | None:
        """读取对象，不存在/不可达返回 None。"""
        if not self.enabled:
            return None
        try:
            resp = self._s3().get_object(Bucket=bucket, Key=key)
            return resp["Body"].read()
        except Exception as exc:  # noqa: BLE001
            log.warning("MinIO get %s/%s 失败：%s", bucket, key, exc)
            return None

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """列出前缀下全部对象键（分页拉全），不可达返回空表。"""
        if not self.enabled:
            return []
        try:
            keys: list[str] = []
            paginator = self._s3().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                keys += [obj["Key"] for obj in page.get("Contents", [])]
            return keys
        except Exception as exc:  # noqa: BLE001
            log.warning("MinIO list %s/%s 失败：%s", bucket, prefix, exc)
            return []

    # ---- 源码快照：目录 ↔ tar.gz 对象 ----

    def snapshot_dir(self, path: Path, bucket: str, key: str) -> str:
        """把检出目录打成 tar.gz 上传（排除 .git 体积黑洞），成功返回键。"""
        if not self.ensure_bucket(bucket) or not path.is_dir():
            return ""
        buf = io.BytesIO()
        try:
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for item in sorted(path.rglob("*")):
                    # 只收文件：tar.add(目录) 会整棵递归子树，逐条排除挡不住孙辈；
                    # 目录由解包端按文件路径隐式创建
                    if not item.is_file() or ".git" in item.parts or "__pycache__" in item.parts:
                        continue
                    tar.add(item, arcname=item.relative_to(path).as_posix())
            return self.put_bytes(bucket, key, buf.getvalue())
        except Exception as exc:  # noqa: BLE001
            log.warning("快照 %s -> %s/%s 失败：%s", path, bucket, key, exc)
            return ""

    def restore_dir(self, path: Path, bucket: str, key: str) -> bool:
        """从快照对象还原检出目录（原子替换：先解到临时目录再换位）。"""
        data = self.get_bytes(bucket, key)
        if data is None:
            return False
        tmp = path.with_name(path.name + ".restore-tmp")
        try:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                tar.extractall(tmp, filter="data")
            if path.exists():
                shutil.rmtree(path)
            tmp.rename(path)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("还原 %s 从 %s/%s 失败：%s", path, bucket, key, exc)
            shutil.rmtree(tmp, ignore_errors=True)
            return False


@functools.lru_cache(maxsize=1)
def get_objectstore() -> ObjectStore:
    return ObjectStore()
