"""仓库接入 URL 门禁单测：本地路径/file:// 默认拒绝，远程 Git URL 放行。"""

import pytest

from app.services.repo.gitops import GitError, validate_remote_url


@pytest.mark.parametrize("url", [
    "https://github.com/user/repo.git",
    "http://git.internal.corp/team/repo.git",
    "git@github.com:user/repo.git",
    "ssh://git@git.internal.corp/team/repo.git",
    "git://example.com/repo.git",
])
def test_remote_urls_allowed(url: str):
    validate_remote_url(url)  # 不抛即通过


@pytest.mark.parametrize("url", [
    "file:///E:/TestForge",
    "file:///mnt/e/TestForge",
    "E:\\TestForge",
    "e:/some/repo",
    "/abs/local/path",
    "../relative/path",
    "\\\\fileserver\\share\\repo",
    "~/repos/x",
    "not a url",
    "",
])
def test_local_paths_rejected_by_default(url: str):
    with pytest.raises(GitError, match="不允许本地路径"):
        validate_remote_url(url)


def test_local_allowed_only_with_explicit_dev_flag():
    # 本地开发/验收夹具场景显式开启后放行（生产 compose 不开）
    validate_remote_url("file:///E:/TestForge", allow_local=True)
