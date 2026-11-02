"""C2 Relationship graph: in-memory, typed, directed, validated. Only ``declared`` edges are trusted (ADR-0002 §3)."""

from __future__ import annotations

from collections.abc import Iterable

from .declarations import Declarations
from .models import ConfigError, Origin, Relationship, Repository


class Graph:
    def __init__(self, repositories: Iterable[Repository], relationships: Iterable[Relationship]) -> None:
        self.repositories: dict[str, Repository] = {}
        for repo in repositories:
            if repo.id in self.repositories:
                raise ConfigError(f"duplicate repository id {repo.id!r}")
            if any(r.slug == repo.slug for r in self.repositories.values()):
                raise ConfigError(f"duplicate repository slug {repo.slug!r}")
            self.repositories[repo.id] = repo
        self._relationships: dict[str, Relationship] = {}
        for rel in relationships:
            self._validate(rel)
            self._relationships[rel.id] = rel

    def _validate(self, rel: Relationship) -> None:
        if rel.id in self._relationships:
            raise ConfigError(f"duplicate relationship id {rel.id!r}")
        for end in (rel.source, rel.target):
            if end not in self.repositories:
                raise ConfigError(f"relationship {rel.id}: unknown repository {end!r}")
        if rel.source == rel.target:
            raise ConfigError(f"relationship {rel.id}: source and target are the same repository")
        for anchor in rel.anchors:
            if anchor.repo not in (rel.source, rel.target):
                raise ConfigError(f"relationship {rel.id}: anchor repository {anchor.repo!r} is not an endpoint")
        if rel.applicability.value == "undetermined" and not rel.open_question:
            raise ConfigError(f"relationship {rel.id}: an undetermined applicability must name its open question")

    @classmethod
    def from_declarations(cls, decl: Declarations) -> Graph:
        return cls(decl.repositories, decl.relationships)

    def get(self, relationship_id: str) -> Relationship:
        return self._relationships[relationship_id]

    def trusted(self) -> list[Relationship]:
        """Declared relationships, the only ones policies may evaluate."""
        return sorted((r for r in self._relationships.values() if r.origin is Origin.DECLARED), key=lambda r: r.id)

    def proposals(self) -> list[Relationship]:
        """Observed or inferred relationships: reported, never evaluated, never traversed."""
        return sorted((r for r in self._relationships.values() if r.origin is not Origin.DECLARED), key=lambda r: r.id)

    def analyzed_ids(self) -> list[str]:
        return sorted(r.id for r in self.repositories.values() if r.analyzed)

    def evaluable(self, rel: Relationship) -> bool:
        return self.repositories[rel.source].analyzed and self.repositories[rel.target].analyzed
