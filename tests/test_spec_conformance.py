"""Every API path used by a tool must exist in ClickUp's OpenAPI specs.

A typo in a path template is invisible until someone makes a live call and gets a
404 — and with 149 tools across two API versions, that is a lot of surface to trust
to proofreading. This walks the AST of every tool module, collects each string that
looks like an API path (including f-strings and the templates held in module-level
dicts), and checks it against the vendored specs.

Specs are vendored at the repo root:
  clickup-api-v2-reference.json   82 paths / 137 operations
  ClickUp_PUBLIC_API_V3.yaml      23 paths /  35 operations  (JSON despite the name)
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "src" / "clickup_mcp" / "tools"

_PATH_RE = re.compile(r"^/v[23]/")


def _normalize(path: str) -> str:
    """Collapse every interpolation to a single placeholder."""
    return re.sub(r"\{[^}]*\}", "{}", path)


_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _spec_methods() -> dict[str, set[str]]:
    """path -> allowed HTTP verbs, across both API versions."""
    spec: dict[str, set[str]] = {}

    v2 = json.loads((REPO / "clickup-api-v2-reference.json").read_text(encoding="utf-8"))
    for path, methods in v2["paths"].items():
        spec[_normalize(path)] = {m.upper() for m in methods if m in _HTTP_METHODS}

    v3 = json.loads((REPO / "ClickUp_PUBLIC_API_V3.yaml").read_text(encoding="utf-8"))
    for path, methods in v3["paths"].items():
        # The v3 server is https://api.clickup.com/ with /api/v3/... paths; this
        # client uses base https://api.clickup.com/api, so it emits /v3/... .
        key = _normalize(path.removeprefix("/api"))
        spec[key] = {m.upper() for m in methods if m in _HTTP_METHODS}

    return spec


def _literal(node: ast.AST) -> str | None:
    """Reconstruct a string node, rendering interpolations as {}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                out.append("{}")
            else:  # pragma: no cover - defensive
                return None
        return "".join(out)
    return None


class _Collector(ast.NodeVisitor):
    """Collect whole string values, never their pieces.

    An f-string's literal segments are child Constant nodes, so a plain
    `ast.walk` yields both the assembled path and fragments like
    "/v2/checklist/". Handling JoinedStr without recursing keeps only the
    assembled form.
    """

    def __init__(self) -> None:
        self.found: list[str] = []

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        text = _literal(node)
        if text is not None:
            self.found.append(text)
        # deliberately no generic_visit — the parts are not paths

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.found.append(node.value)


def _used_paths() -> dict[str, set[str]]:
    """path -> {modules that use it}. Covers direct call arguments and the path
    templates some modules keep in module-level dicts."""
    found: dict[str, set[str]] = {}
    for module in sorted(TOOLS.glob("*.py")):
        collector = _Collector()
        collector.visit(ast.parse(module.read_text(encoding="utf-8")))
        for text in collector.found:
            if _PATH_RE.match(text):
                found.setdefault(_normalize(text), set()).add(module.name)
    return found


_VERBS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "post_multipart": "POST",
}


def _call_sites() -> list[tuple[str, str, str]]:
    """(module, verb, path) for every direct client.<verb>("/v2/...") call."""
    sites: list[tuple[str, str, str]] = []
    for module in sorted(TOOLS.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr not in _VERBS:
                continue
            if not node.args:
                continue
            path = _literal(node.args[0])
            if path and _PATH_RE.match(path):
                sites.append((module.name, _VERBS[node.func.attr], _normalize(path)))
    return sites


SPEC_METHODS = _spec_methods()
SPEC_PATHS = set(SPEC_METHODS)
USED_PATHS = _used_paths()
CALL_SITES = _call_sites()

# Reachable in the spec but deliberately not exposed as tools.
_INTENTIONALLY_UNEXPOSED = {
    "/v2/oauth/token",  # the OAuth exchange itself, driven by oauth_provider.py
    "/v2/user",         # identity lookup at authorization time; surfaced via whoami
}


def test_the_specs_actually_loaded():
    assert len(SPEC_PATHS) >= 100, f"only parsed {len(SPEC_PATHS)} spec paths"


def test_tools_reference_a_meaningful_share_of_the_api():
    assert len(USED_PATHS) >= 70, f"only found {len(USED_PATHS)} paths in the tools"


@pytest.mark.parametrize("path", sorted(USED_PATHS))
def test_path_exists_in_the_clickup_spec(path):
    assert path in SPEC_PATHS, (
        f"{path} is not in either ClickUp spec "
        f"(used by {', '.join(sorted(USED_PATHS[path]))})"
    )


@pytest.mark.parametrize("module,verb,path", CALL_SITES, ids=lambda v: str(v))
def test_verb_is_allowed_on_that_path(module, verb, path):
    """A correct path with the wrong verb still 405s at runtime."""
    allowed = SPEC_METHODS.get(path)
    assert allowed is not None, f"{module}: {path} is not in either spec"
    assert verb in allowed, (
        f"{module}: {verb} {path} — ClickUp allows only {sorted(allowed)}"
    )


def test_every_spec_endpoint_is_either_exposed_or_deliberately_skipped():
    """Coverage guard. Full CRUD across all four phases was the agreed scope, so a
    newly-unexposed endpoint means something was dropped, not merely deferred."""
    missing = sorted(SPEC_PATHS - set(USED_PATHS) - _INTENTIONALLY_UNEXPOSED)
    assert not missing, (
        f"{len(missing)} spec endpoint(s) have no tool: {missing}. "
        "Add a tool, or add the path to _INTENTIONALLY_UNEXPOSED with a reason."
    )
