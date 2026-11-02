"""Declared locators: a closed set of extractor kinds, validated at load time, never an expression language."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import extract
from .extract import Extracted, ExtractionError
from .models import Cause, ConfigError

_REQUIRED: dict[str, tuple[str, ...]] = {
    "js_const_int": ("name",),
    "js_const_string": ("name",),
    "js_object_keys": ("after", "literal"),
    "js_object_value": ("after", "literal", "key"),
    "yaml_path": ("yaml_path",),
}
_REGEX_PARAMS = {"after", "literal"}


@dataclass(frozen=True)
class LocatorSpec:
    kind: str
    repo: str
    path: str
    params: Mapping[str, str]

    @property
    def label(self) -> str:
        detail = (
            self.params.get("name")
            or self.params.get("yaml_path")
            or self.params.get("key")
            or self.params.get("after", "")
        )
        return f"{self.kind}:{detail}"

    def resolve(self, text: str) -> Extracted:
        p = self.params
        match self.kind:
            case "js_const_int":
                return extract.js_const_int(text, p["name"])
            case "js_const_string":
                return extract.js_const_string(text, p["name"])
            case "js_object_keys":
                return extract.js_object_keys(text, p["after"], p["literal"])
            case "js_object_value":
                return extract.js_object_value(text, p["after"], p["literal"], p["key"])
            case "yaml_path":
                value, quote, start, end = extract.yaml_single(text).get(p["yaml_path"])
                return Extracted(value, quote, start, end, f"yaml_path@{extract.EXTRACTOR_VERSION}")
        raise ExtractionError(Cause.LOCATOR_INVALID, f"unknown locator kind {self.kind!r}")


def parse_locator(raw: Any, where: str, allowed_repos: set[str]) -> LocatorSpec:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: locator must be a mapping")
    kind = raw.get("kind")
    if kind not in _REQUIRED:
        raise ConfigError(f"{where}: unknown locator kind {kind!r} (allowed: {sorted(_REQUIRED)})")
    repo, path = raw.get("repo"), raw.get("path")
    if not isinstance(repo, str) or repo not in allowed_repos:
        raise ConfigError(f"{where}: 'repo' must be one of {sorted(allowed_repos)}")
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
        raise ConfigError(f"{where}: invalid 'path'")
    params: dict[str, str] = {}
    for name in _REQUIRED[str(kind)]:
        value = raw.get(name)
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{where}: '{name}' is required for {kind}")
        if name in _REGEX_PARAMS:
            try:
                re.compile(value)
            except re.error as exc:
                raise ConfigError(f"{where}: invalid regex in '{name}': {exc}") from exc
        params[name] = value
    return LocatorSpec(str(kind), repo, path, params)
