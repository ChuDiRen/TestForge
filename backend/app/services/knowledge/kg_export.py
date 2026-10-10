"""KG 导出（LightRAG export API 对齐）：实体/关系/社区/chunk/全图 → csv | md | json | graphml | xlsx。

返回 (filename, content_bytes, content_type)；xlsx/graphml 为二进制，json/csv/md 为 UTF-8 文本。
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime

from app.db.session import get_session
from app.models import KgCommunity, KgEntity, KgRelation

EXPORT_WHATS = ("entities", "relations", "communities", "chunks", "graph")


def _scope_filters(repo_id: int, workspace: str) -> tuple[str, dict]:
    if workspace:
        return "workspace = :ws", {"ws": workspace}
    return "repo_id = :rid", {"rid": repo_id}


def _entities(repo_id: int, workspace: str) -> list[dict]:
    with get_session() as sess:
        q = sess.query(KgEntity)
        q = q.filter(KgEntity.workspace == workspace) if workspace else q.filter(KgEntity.repo_id == repo_id)
        rows = q.order_by(KgEntity.name).all()
    return [
        {"name": r.name, "type": r.etype, "description": r.description, "source_refs": json.loads(r.source_refs or "[]")}
        for r in rows
    ]


def _relations(repo_id: int, workspace: str) -> list[dict]:
    with get_session() as sess:
        q = sess.query(KgRelation)
        q = q.filter(KgRelation.workspace == workspace) if workspace else q.filter(KgRelation.repo_id == repo_id)
        rows = q.order_by(KgRelation.id).all()
    return [
        {"src": r.src_name, "dst": r.dst_name, "type": r.rtype, "description": r.description, "weight": r.weight}
        for r in rows
    ]


def _communities(repo_id: int, workspace: str) -> list[dict]:
    with get_session() as sess:
        q = sess.query(KgCommunity)
        q = q.filter(KgCommunity.workspace == workspace) if workspace else q.filter(KgCommunity.repo_id == repo_id)
        rows = q.order_by(KgCommunity.level, KgCommunity.cluster_id).all()
    return [
        {"level": r.level, "cluster_id": r.cluster_id, "title": r.title, "summary": r.summary, "member_count": r.member_count, "members": json.loads(r.members or "[]")}
        for r in rows
    ]


def _chunks(repo_id: int, workspace: str) -> list[dict]:
    from sqlalchemy import text

    from app.services.knowledge.rag import _tables_ready

    out: list[dict] = []
    with get_session() as sess:
        if not _tables_ready(sess):
            return out
        w, p = ("workspace = :ws", {"ws": workspace}) if workspace else ("repo_id = :rid", {"rid": repo_id})
        rows = sess.execute(
            text(f"SELECT doc_key, title, content, meta FROM rag_documents WHERE kind = 'kg_chunk' AND {w} ORDER BY doc_key"),
            p,
        ).all()
    for r in rows:
        meta = r[3] if isinstance(r[3], dict) else {}
        out.append({"doc_key": r[0], "title": r[1], "content": r[2], "parent": meta.get("parent", ""), "index": meta.get("index", 0)})
    return out


def _graph(repo_id: int, workspace: str) -> dict:
    ents = _entities(repo_id, workspace)
    rels = _relations(repo_id, workspace)
    return {
        "directed": False,
        "multigraph": False,
        "nodes": [{"id": e["name"], "label": e["name"], "type": e["type"], "sources": len(e["source_refs"])} for e in ents],
        "edges": [{"source": r["src"], "target": r["dst"], "type": r["type"], "weight": r["weight"]} for r in rels],
        "communities": _communities(repo_id, workspace),
    }


def _to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in r.items()})
    return buf.getvalue()


def _to_md(title: str, rows: list[dict]) -> str:
    if not rows:
        return f"# {title}\n\n（空）\n"
    keys = list(rows[0].keys())
    lines = [f"# {title}", "", "| " + " | ".join(keys) + " |", "| " + " | ".join(["---"] * len(keys)) + " |"]
    for r in rows:
        cells = []
        for k in keys:
            v = r[k]
            text = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v or "")
            cells.append(text.replace("|", "\\|").replace("\n", " ")[:120])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _to_xlsx(title: str, rows: list[dict]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = title[:30]
    if rows:
        keys = list(rows[0].keys())
        ws.append(keys)
        for r in rows:
            ws.append([json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in (r.get(k) for k in keys)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _to_graphml(data: dict) -> bytes:
    import networkx as nx

    g = nx.Graph()
    for n in data["nodes"]:
        g.add_node(n["id"], label=n["label"], etype=n.get("type", ""))
    for e in data["edges"]:
        if g.has_node(e["source"]) and g.has_node(e["target"]):
            g.add_edge(e["source"], e["target"], rtype=e.get("type", ""), weight=e.get("weight", 1.0))
    buf = io.BytesIO()
    nx.write_graphml(g, buf, encoding="utf-8")
    return buf.getvalue()


def export(what: str, fmt: str, repo_id: int = 0, workspace: str = "") -> tuple[str, bytes, str]:
    """导出入口。返回 (filename, bytes, content_type)。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    what = what if what in EXPORT_WHATS else "entities"
    fmt = (fmt or "json").lower()

    if what == "graph":
        data = _graph(repo_id, workspace)
        if fmt == "graphml":
            return f"kg_graph_{stamp}.graphml", _to_graphml(data), "application/graphml+xml"
        if fmt == "zip":
            # 全图打包：graph.json + entities.csv + relations.csv + communities.csv
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("graph.json", json.dumps(data, ensure_ascii=False, indent=2))
                z.writestr("entities.csv", _to_csv(_entities(repo_id, workspace)))
                z.writestr("relations.csv", _to_csv(_relations(repo_id, workspace)))
                z.writestr("communities.csv", _to_csv(_communities(repo_id, workspace)))
            return f"kg_graph_{stamp}.zip", buf.getvalue(), "application/zip"
        body = json.dumps(data, ensure_ascii=False, indent=2)
        return f"kg_graph_{stamp}.json", body.encode("utf-8"), "application/json"

    rows = _entities(repo_id, workspace) if what == "entities" else _relations(repo_id, workspace) if what == "relations" else _communities(repo_id, workspace) if what == "communities" else _chunks(repo_id, workspace)
    title = {"entities": "知识实体", "relations": "知识关系", "communities": "社区报告", "chunks": "文档分块"}[what]
    if fmt == "csv":
        return f"kg_{what}_{stamp}.csv", _to_csv(rows).encode("utf-8-sig"), "text/csv"  # BOM：Excel 直开不乱码
    if fmt == "md":
        return f"kg_{what}_{stamp}.md", _to_md(title, rows).encode("utf-8"), "text/markdown"
    if fmt == "xlsx":
        return f"kg_{what}_{stamp}.xlsx", _to_xlsx(title, rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    body = json.dumps(rows, ensure_ascii=False, indent=2)
    return f"kg_{what}_{stamp}.json", body.encode("utf-8"), "application/json"
