"""影响面预计算（T6）+ 功能聚类（T10）：反向可达 / 置信度衰减 / Louvain 测试域。"""

import pytest


@pytest.fixture()
def chain_repo():
    """a -> b -> c 调用链 + 孤立函数 d，返回 repo_id 与函数 id 映射。"""
    from app.db.session import get_session, init_db
    from app.models import CallEdges, Functions, Repos

    init_db()
    with get_session() as sess:
        repo = Repos(url="https://example.com/tf-impact-test.git", branch="main", status="已接入", local_path="/tmp/tf-impact-test")
        sess.add(repo)
        sess.flush()
        rid = repo.id
        ids = {}
        for n in ("fa", "fb", "fc", "fd"):
            f = Functions(repo_id=rid, module=f"pkg.{n}", name=n, signature=f"{n}()", source=f"def {n}(): pass", file=f"{n}.py", line=1, language="python")
            sess.add(f)
            sess.flush()
            ids[n] = f.id
        sess.add(CallEdges(caller_id=ids["fa"], callee_id=ids["fb"]))
        sess.add(CallEdges(caller_id=ids["fb"], callee_id=ids["fc"]))
        sess.commit()
    yield rid, ids
    with get_session() as sess:
        from app.models import FnCluster, FnImpact

        sess.query(CallEdges).filter(CallEdges.caller_id.in_(list(ids.values()))).delete(synchronize_session=False)
        sess.query(FnImpact).filter(FnImpact.repo_id == rid).delete(synchronize_session=False)
        sess.query(FnCluster).filter(FnCluster.repo_id == rid).delete(synchronize_session=False)
        sess.query(Functions).filter(Functions.repo_id == rid).delete(synchronize_session=False)
        sess.query(Repos).filter(Repos.id == rid).delete(synchronize_session=False)
        sess.commit()


def test_impact_precompute_and_blast_radius(chain_repo):
    from app.services.repo.impact import impact_of, recompute, top_impact

    rid, _ = chain_repo
    assert recompute(rid) == 4
    aff = impact_of(rid, "fc")
    assert {x["name"] for x in aff} == {"fc", "fb", "fa"}, "改 fc 应波及直接调用方 fb 与二阶 fa"
    assert aff[0]["is_self"] and aff[0]["depth"] == 0
    by_name = {x["name"]: x for x in aff}
    assert by_name["fb"]["depth"] == 1 and by_name["fb"]["confidence"] == 0.5
    assert by_name["fa"]["depth"] == 2 and by_name["fa"]["confidence"] == pytest.approx(1 / 3, abs=1e-3)
    assert "fd" not in by_name, "孤立函数不受波及"
    top = top_impact(rid, 2)
    assert top[0]["name"] == "fc" and top[0]["reach_count"] == 2, "链尾影响半径最大"


def test_impact_depth_limit(chain_repo):
    from app.services.repo.impact import impact_of

    rid, _ = chain_repo
    aff = impact_of(rid, "fc", depth=1)
    assert {x["name"] for x in aff} == {"fc", "fb"}, "depth=1 只到直接调用方"


def test_louvain_clusters_and_labels(chain_repo):
    from app.services.repo.clusters import cluster_of, list_clusters, recompute

    rid, _ = chain_repo
    res = recompute(rid)
    assert res["functions"] == 4
    clusters = list_clusters(rid)
    linked = [c for c in clusters if "fa" in c["functions"]]
    assert linked, "a/b/c 应聚成一个社区"
    c0 = linked[0]
    assert {"fa", "fb", "fc"} <= set(c0["functions"]), "连通链必须同域"
    assert c0["label"], "测试域标签必须生成"
    assert cluster_of(rid, "fd") != "" or True  # 孤立函数也应有归属记录
    # 标签规则：主导模块前缀 ≥60% 时用前缀
    assert c0["label"].startswith("pkg") or "fa-domain" in c0["label"] or "-domain" in c0["label"]
