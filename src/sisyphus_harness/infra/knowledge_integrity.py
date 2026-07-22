from __future__ import annotations

import heapq
import sqlite3

from ..contracts.codec import loads_strict_json, sha256_digest
from ..contracts.knowledge import (
    DEPENDENCY_EDGE_TYPES,
    DERIVED_CANDIDATE_AUTHORITY,
    KnowledgeEdge,
    KnowledgeNode,
    weighted_node_terms,
)
from .knowledge_index_errors import KnowledgeIndexError


def revision_digest_from_connection(
    connection: sqlite3.Connection,
    *,
    schema_version: int,
) -> str:
    metadata_rows = connection.execute(
        """
        SELECT key, value
        FROM knowledge_index_metadata
        ORDER BY key
        """
    ).fetchall()
    node_rows = connection.execute(
        "SELECT * FROM knowledge_nodes ORDER BY node_id"
    ).fetchall()
    edge_rows = connection.execute(
        """
        SELECT *
        FROM knowledge_edges
        ORDER BY source_node_id, target_node_id, edge_type
        """
    ).fetchall()
    term_rows = connection.execute(
        """
        SELECT node_id, term, weight
        FROM knowledge_terms
        ORDER BY node_id, term
        """
    ).fetchall()
    stored_metadata = tuple((row["key"], row["value"]) for row in metadata_rows)
    expected_metadata = (
        ("authority", DERIVED_CANDIDATE_AUTHORITY),
        ("schema_version", str(schema_version)),
    )
    if stored_metadata != expected_metadata:
        raise KnowledgeIndexError(
            "knowledge index metadata does not match the supported schema"
        )
    nodes = tuple(node_from_row(row) for row in node_rows)
    edges = tuple(edge_from_row(row) for row in edge_rows)
    nodes_by_id = {node.node_id: node for node in nodes}
    for edge in edges:
        source = nodes_by_id.get(edge.source_node_id)
        target = nodes_by_id.get(edge.target_node_id)
        if source is None or target is None:
            raise KnowledgeIndexError(
                "knowledge edge endpoints do not reference stored nodes"
            )
        if edge.edge_type in DEPENDENCY_EDGE_TYPES and (
            source.node_type.value != "task" or target.node_type.value != "task"
        ):
            raise KnowledgeIndexError(
                "dependency edge endpoints are not stored task nodes"
            )
    if dependency_edges_contain_cycle(edges):
        raise KnowledgeIndexError("knowledge dependency edges contain a cycle")
    expected_terms = tuple(
        (node.node_id, term, weight)
        for node in nodes
        for term, weight in weighted_node_terms(node).items()
    )
    stored_terms = tuple(
        (row["node_id"], row["term"], row["weight"]) for row in term_rows
    )
    if stored_terms != expected_terms:
        raise KnowledgeIndexError(
            "knowledge term index does not match stored node payloads"
        )
    return sha256_digest(
        {
            "metadata": [list(item) for item in stored_metadata],
            "nodes": [[node.node_id, node.node_digest] for node in nodes],
            "edges": [
                [
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.edge_type.value,
                    edge.edge_digest,
                ]
                for edge in edges
            ],
            "terms": [list(item) for item in stored_terms],
        }
    )


def canonical_node(node: KnowledgeNode) -> KnowledgeNode:
    if type(node) is not KnowledgeNode:
        raise TypeError("knowledge index requires an exact KnowledgeNode")
    return KnowledgeNode.from_dict(node.to_dict())


def canonical_edge(edge: KnowledgeEdge) -> KnowledgeEdge:
    if type(edge) is not KnowledgeEdge:
        raise TypeError("knowledge index requires an exact KnowledgeEdge")
    return KnowledgeEdge.from_dict(edge.to_dict())


def dependency_edges_contain_cycle(edges: tuple[KnowledgeEdge, ...]) -> bool:
    dependency_edges = tuple(
        edge for edge in edges if edge.edge_type in DEPENDENCY_EDGE_TYPES
    )
    if not dependency_edges:
        return False
    node_ids = {
        node_id
        for edge in dependency_edges
        for node_id in (edge.source_node_id, edge.target_node_id)
    }
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    incoming_count = {node_id: 0 for node_id in node_ids}
    for edge in dependency_edges:
        outgoing[edge.source_node_id].append(edge.target_node_id)
        incoming_count[edge.target_node_id] += 1
    ready = [
        node_id for node_id, count in incoming_count.items() if count == 0
    ]
    heapq.heapify(ready)
    visited = 0
    while ready:
        current = heapq.heappop(ready)
        visited += 1
        for target in sorted(outgoing[current]):
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                heapq.heappush(ready, target)
    return visited != len(node_ids)


def node_from_row(row: sqlite3.Row) -> KnowledgeNode:
    node = _parse_node(row["payload_json"])
    expected = {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "task_status": (
            None if node.task_status is None else node.task_status.value
        ),
        "title": node.title,
        "content_digest": node.content_digest,
        "metadata_digest": node.metadata_digest,
        "provenance_digest": node.provenance_digest,
        "node_digest": node.node_digest,
        "authority": node.authority,
    }
    if any(row[key] != value for key, value in expected.items()):
        raise KnowledgeIndexError(
            "stored knowledge node columns do not match its payload"
        )
    return node


def edge_from_row(row: sqlite3.Row) -> KnowledgeEdge:
    edge = _parse_edge(row["payload_json"])
    expected = {
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "edge_type": edge.edge_type.value,
        "metadata_digest": edge.metadata_digest,
        "provenance_digest": edge.provenance_digest,
        "edge_digest": edge.edge_digest,
        "authority": edge.authority,
    }
    if any(row[key] != value for key, value in expected.items()):
        raise KnowledgeIndexError(
            "stored knowledge edge columns do not match its payload"
        )
    return edge


def parse_schema_version(raw: object) -> int:
    try:
        version = int(str(raw))
    except ValueError as exc:
        raise KnowledgeIndexError(
            f"invalid knowledge index schema version: {raw}"
        ) from exc
    if version < 0:
        raise KnowledgeIndexError(f"invalid knowledge index schema version: {raw}")
    return version


def _parse_node(payload: str) -> KnowledgeNode:
    try:
        return KnowledgeNode.from_dict(
            loads_strict_json(payload, label="stored knowledge node")
        )
    except ValueError as exc:
        raise KnowledgeIndexError(str(exc)) from exc


def _parse_edge(payload: str) -> KnowledgeEdge:
    try:
        return KnowledgeEdge.from_dict(
            loads_strict_json(payload, label="stored knowledge edge")
        )
    except ValueError as exc:
        raise KnowledgeIndexError(str(exc)) from exc
