from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3

from ....authority import knowledge_index_path
from ....contracts.knowledge import (
    DERIVED_CANDIDATE_AUTHORITY,
    KnowledgeEdge,
    KnowledgeNode,
)
from ....infra.knowledge_index import KnowledgeIndexError, SQLiteKnowledgeIndex
from ....knowledge_graph import KnowledgeGraph
from ..io import strict_json_object
from ..result import CliResult


def handle_knowledge(args: argparse.Namespace, repo_root: Path) -> CliResult:
    if args.command == "graph-init":
        index = SQLiteKnowledgeIndex(knowledge_index_path(repo_root))
        index.initialize()
        return CliResult(
            {
                "authority": DERIVED_CANDIDATE_AUTHORITY,
                "index_path": str(index.path),
                "index_revision_digest": index.revision_digest(),
                "status": "initialized",
            }
        )
    if args.command == "graph-put-node":
        index = _writable_knowledge_index(repo_root)
        node = KnowledgeNode.from_dict(
            strict_json_object(args.node_json, "--node-json")
        )
        indexed = KnowledgeGraph(index).add_node(node)
        return CliResult(
            {
                "authority": DERIVED_CANDIDATE_AUTHORITY,
                "index_revision_digest": index.revision_digest(),
                "indexed": indexed,
                "node": node.to_dict(),
            }
        )
    if args.command == "graph-put-edge":
        index = _writable_knowledge_index(repo_root)
        edge = KnowledgeEdge.from_dict(
            strict_json_object(args.edge_json, "--edge-json")
        )
        indexed = KnowledgeGraph(index).add_edge(edge)
        return CliResult(
            {
                "authority": DERIVED_CANDIDATE_AUTHORITY,
                "edge": edge.to_dict(),
                "index_revision_digest": index.revision_digest(),
                "indexed": indexed,
            }
        )
    if args.command == "graph-search":
        index = _readable_knowledge_index(repo_root)
        revision = index.revision_digest()
        hits = KnowledgeGraph(index).search(
            args.anchor_id,
            args.query,
            max_depth=args.max_depth,
            limit=args.limit,
        )
        if index.revision_digest() != revision:
            raise RuntimeError(
                "knowledge index changed while graph-search was being rendered"
            )
        return CliResult(
            {
                "anchor_id": args.anchor_id,
                "authority": DERIVED_CANDIDATE_AUTHORITY,
                "hits": [hit.to_dict() for hit in hits],
                "index_revision_digest": revision,
                "max_depth": args.max_depth,
                "query": args.query,
            }
        )
    if args.command == "graph-dependencies":
        index = _readable_knowledge_index(repo_root)
        revision = index.revision_digest()
        inspection = KnowledgeGraph(index).inspect_dependencies(
            args.task_id,
            max_depth=args.max_depth,
        )
        if index.revision_digest() != revision:
            raise RuntimeError(
                "knowledge index changed while dependencies were being rendered"
            )
        return CliResult(
            {
                "authority": DERIVED_CANDIDATE_AUTHORITY,
                "index_revision_digest": revision,
                "inspection": inspection.to_dict(),
            }
        )
    if args.command == "graph-next":
        index = _readable_knowledge_index(repo_root)
        context = KnowledgeGraph(index).next_step_context(
            args.anchor_id,
            args.query,
            max_depth=args.max_depth,
            dependency_max_depth=args.dependency_max_depth,
            limit=args.limit,
        )
        return CliResult(context.to_dict())
    raise AssertionError(f"unhandled knowledge command: {args.command}")


def _writable_knowledge_index(repo_root: Path) -> SQLiteKnowledgeIndex:
    index = SQLiteKnowledgeIndex(knowledge_index_path(repo_root))
    index.initialize()
    return index


def _readable_knowledge_index(repo_root: Path) -> SQLiteKnowledgeIndex:
    path = knowledge_index_path(repo_root)
    if not path.is_file():
        raise ValueError("knowledge index is not initialized; run graph-init first")
    index = SQLiteKnowledgeIndex(path)
    try:
        index.revision_digest()
    except (KnowledgeIndexError, sqlite3.Error) as exc:
        raise ValueError(
            "knowledge index is not initialized or failed integrity validation"
        ) from exc
    return index
