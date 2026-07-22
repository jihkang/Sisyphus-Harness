from __future__ import annotations


class KnowledgeIndexError(RuntimeError):
    pass


class KnowledgeIndexConflict(KnowledgeIndexError):
    pass
