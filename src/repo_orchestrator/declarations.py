"""Version-controlled declarations (ADR-0008): repositories, relationships, policy parameters, exceptions, pins.

Everything is validated at load time and fails with :class:`ConfigError` naming the file and field, before any
repository is collected.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .models import (
    Anchor,
    Applicability,
    ConfigError,
    Origin,
    Relationship,
    RelType,
    Repository,
    digest,
)

MAX_DECLARATION_BYTES = 512 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise ConfigError(f"duplicate key {key!r} (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return super().construct_mapping(node, deep)


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"{path.name}: cannot read ({exc.strerror})") from exc
    if len(raw) > MAX_DECLARATION_BYTES:
        raise ConfigError(f"{path.name}: larger than {MAX_DECLARATION_BYTES} bytes")
    try:
        data = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path.name}: invalid YAML ({str(exc).splitlines()[0][:100]})") from exc
    except ConfigError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    if data.get("version") != 1:
        raise ConfigError(f"{path.name}: unsupported or missing version (expected 1)")
    return data


def field_of(
    m: Mapping[str, Any], key: str, kind: type | tuple[type, ...], where: str, *, required: bool = True
) -> Any:
    if key not in m or m[key] is None:
        if required:
            raise ConfigError(f"{where}: missing '{key}'")
        return None
    value = m[key]
    if isinstance(value, bool) and kind is not bool and bool not in (kind if isinstance(kind, tuple) else (kind,)):
        raise ConfigError(f"{where}: '{key}' has the wrong type")
    if not isinstance(value, kind):
        raise ConfigError(
            f"{where}: '{key}' must be {kind.__name__ if isinstance(kind, type) else 'of an allowed type'}"
        )
    return value


@dataclass(frozen=True)
class AuthorityRef:
    repo: str
    path: str
    quote: str | None = None


@dataclass(frozen=True)
class Question:
    id: str
    status: str
    text: str
    effect: str


@dataclass(frozen=True)
class PinSet:
    name: str
    description: str
    revisions: Mapping[str, str]


@dataclass(frozen=True)
class ExceptionDecl:
    id: str
    policy: str
    relationships: tuple[str, ...]
    subject: str | None
    justification: str
    authority: AuthorityRef
    owner: str
    review_by: date


@dataclass(frozen=True)
class RepositoryAuthority:
    id: str
    repo: str
    policy: str
    relationships: tuple[str, ...]
    subject: str | None
    statement: str
    authority: AuthorityRef


@dataclass(frozen=True)
class Declarations:
    repositories: tuple[Repository, ...]
    relationships: tuple[Relationship, ...]
    policy_params: Mapping[str, Mapping[str, Any]]
    exceptions: tuple[ExceptionDecl, ...]
    repository_authorities: tuple[RepositoryAuthority, ...]
    questions: tuple[Question, ...]
    pins: Mapping[str, PinSet]
    digest: str

    def question(self, qid: str) -> Question | None:
        return next((q for q in self.questions if q.id == qid), None)


def parse_authority(raw: Any, where: str) -> AuthorityRef:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: authority must be a mapping")
    quote = field_of(raw, "quote", str, where, required=False)
    return AuthorityRef(field_of(raw, "repo", str, where), field_of(raw, "path", str, where), quote)


def _repositories(data: dict[str, Any]) -> tuple[Repository, ...]:
    repos = []
    for i, raw in enumerate(field_of(data, "repositories", list, "repositories.yaml")):
        where = f"repositories.yaml[{i}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: must be a mapping")
        slug = field_of(raw, "slug", str, where)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
            raise ConfigError(f"{where}: invalid slug {slug!r}")
        repos.append(
            Repository(
                field_of(raw, "id", str, where),
                slug,
                field_of(raw, "scope", str, where),
                bool(raw.get("analyzed", True)),
                str(raw.get("note", "")),
                str(raw.get("default_branch", "main")),
            )
        )
    return tuple(repos)


def _relationships(data: dict[str, Any]) -> tuple[Relationship, ...]:
    rels = []
    for i, raw in enumerate(field_of(data, "relationships", list, "relationships.yaml")):
        where = f"relationships.yaml[{i}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: must be a mapping")
        rid = field_of(raw, "id", str, where)
        where = f"relationships.yaml:{rid}"
        try:
            rtype, origin = RelType(str(raw.get("type"))), Origin(str(raw.get("origin")))
            applicability = Applicability(str(raw.get("applicability", "in_scope")))
        except ValueError as exc:
            raise ConfigError(f"{where}: {exc}") from exc
        anchors = tuple(
            Anchor(field_of(a, "repo", str, where), field_of(a, "path", str, where), str(a.get("role", "")))
            for a in field_of(raw, "anchors", list, where, required=False) or []
        )
        contract = field_of(raw, "contract", dict, where, required=False) or {}
        rels.append(
            Relationship(
                rid,
                field_of(raw, "baseline", str, where),
                rtype,
                field_of(raw, "source", str, where),
                field_of(raw, "target", str, where),
                origin,
                field_of(raw, "description", str, where),
                applicability,
                field_of(raw, "open_question", str, where, required=False),
                anchors,
                contract,
                str(raw.get("evidence", "")),
            )
        )
    return tuple(rels)


def _exceptions(data: dict[str, Any]) -> tuple[ExceptionDecl, ...]:
    out = []
    for i, raw in enumerate(field_of(data, "exceptions", list, "exceptions.yaml")):
        where = f"exceptions.yaml[{i}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: must be a mapping")
        review = raw.get("review_by")
        if not isinstance(review, date):
            raise ConfigError(f"{where}: 'review_by' must be a date (YYYY-MM-DD)")
        rels = field_of(raw, "relationships", list, where)
        if not rels or any(not isinstance(r, str) or "*" in r for r in rels):
            raise ConfigError(f"{where}: 'relationships' must list explicit relationship ids (no wildcard)")
        out.append(
            ExceptionDecl(
                field_of(raw, "id", str, where),
                field_of(raw, "policy", str, where),
                tuple(rels),
                field_of(raw, "subject", str, where, required=False),
                field_of(raw, "justification", str, where).strip() or _empty(where, "justification"),
                parse_authority(raw.get("authority"), f"{where}.authority"),
                field_of(raw, "owner", str, where).strip() or _empty(where, "owner"),
                review,
            )
        )
    return tuple(out)


def _empty(where: str, key: str) -> str:
    raise ConfigError(f"{where}: '{key}' must not be empty")


def _authorities(data: dict[str, Any]) -> tuple[RepositoryAuthority, ...]:
    out = []
    for i, raw in enumerate(field_of(data, "repository_authorities", list, "policies.yaml", required=False) or []):
        where = f"policies.yaml:repository_authorities[{i}]"
        out.append(
            RepositoryAuthority(
                field_of(raw, "id", str, where),
                field_of(raw, "repo", str, where),
                field_of(raw, "policy", str, where),
                tuple(field_of(raw, "relationships", list, where)),
                field_of(raw, "subject", str, where, required=False),
                field_of(raw, "statement", str, where),
                parse_authority(raw.get("authority"), f"{where}.authority"),
            )
        )
    return tuple(out)


def _pins(data: dict[str, Any]) -> dict[str, PinSet]:
    pins = {}
    for name, raw in field_of(data, "sets", dict, "pins.yaml").items():
        revisions = field_of(raw, "revisions", dict, f"pins.yaml:{name}")
        for repo_id, sha in revisions.items():
            if not isinstance(sha, str) or not SHA_RE.match(sha):
                raise ConfigError(f"pins.yaml:{name}.{repo_id}: not a 40-hex commit SHA")
        pins[str(name)] = PinSet(str(name), str(raw.get("description", "")), dict(revisions))
    return pins


def _questions(data: dict[str, Any]) -> tuple[Question, ...]:
    out = []
    for i, raw in enumerate(field_of(data, "questions", list, "questions.yaml")):
        where = f"questions.yaml[{i}]"
        out.append(
            Question(
                field_of(raw, "id", str, where),
                field_of(raw, "status", str, where),
                field_of(raw, "text", str, where),
                field_of(raw, "effect", str, where),
            )
        )
    return tuple(out)


def load_declarations(directory: Path) -> Declarations:
    docs = {
        name: read_yaml(directory / f"{name}.yaml")
        for name in ("repositories", "relationships", "policies", "exceptions", "questions", "pins")
    }
    policies = field_of(docs["policies"], "policies", dict, "policies.yaml")
    decl = Declarations(
        _repositories(docs["repositories"]),
        _relationships(docs["relationships"]),
        {str(k): v for k, v in policies.items()},
        _exceptions(docs["exceptions"]),
        _authorities(docs["policies"]),
        _questions(docs["questions"]),
        _pins(docs["pins"]),
        digest(docs),
    )
    return decl
