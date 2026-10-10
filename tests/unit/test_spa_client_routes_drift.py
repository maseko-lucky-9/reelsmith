"""``CLIENT_ROUTES`` mirrors the React router (T036).

``app/spa_fallback.py`` keeps a Python copy of the paths the React router
renders, so the server can tell a reload of a client route from an API call.
These tests read ``web/src/routeTree.ts`` and the route files it imports as
plain text (no node needed) and fail when the two lists drift apart. They also
check the table against the API: a fixed API GET path (no ``{param}``) that a
client route would capture must be listed in ``API_ONLY_PATHS``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.main import create_app
from app.spa_fallback import API_ONLY_PATHS, CLIENT_ROUTES, compile_route

WEB_SRC = Path(__file__).parents[2] / "web" / "src"

_IMPORT = re.compile(
    r"""import\s*\{\s*(\w+)\s*\}\s*from\s*['"]\./routes/([\w.$-]+)['"]"""
)
_ROOT_CHILDREN = re.compile(r"rootRoute\.addChildren\(\s*\[(.*?)\]\s*\)", re.S)
_CREATE_ROUTE = re.compile(r"export\s+const\s+(\w+)\s*=\s*createRoute\(")
_PATH = re.compile(r"""\bpath:\s*['"]([^'"]+)['"]""")
_PARENT = re.compile(r"getParentRoute:\s*\(\)\s*=>\s*(\w+)")


def _call_arguments(source: str, start: int) -> str:
    """Text between ``source[start - 1]`` (an opening parenthesis) and its
    matching close, skipping string literals."""
    depth, i, quote = 1, start, ""
    while i < len(source):
        ch = source[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[start:i]
        i += 1
    raise AssertionError("unbalanced createRoute(...)")


def _route_definitions(source: str) -> dict[str, str]:
    """``export const X = createRoute(...)`` → the text of its argument."""
    return {
        m.group(1): _call_arguments(source, m.end())
        for m in _CREATE_ROUTE.finditer(source)
    }


def parse_client_routes(web_src: Path) -> list[str]:
    """Return the path of every route registered in ``routeTree.ts``.

    Supports the tree as written today: a flat list of children of
    ``rootRoute``, each a ``createRoute`` with a literal ``path``. Anything
    else (nested children, a computed path) fails loudly so the parser is
    extended rather than silently missing a route.
    """
    tree = (web_src / "routeTree.ts").read_text(encoding="utf-8")
    imports = dict(_IMPORT.findall(tree))
    assert tree.count(".addChildren(") == 1, "nested route children: extend the parser"
    [children_src] = _ROOT_CHILDREN.findall(tree)
    children = [name.strip() for name in children_src.split(",") if name.strip()]
    assert children, "no routes found in routeTree.ts"

    paths: list[str] = []
    for name in children:
        assert name in imports, f"{name} is not imported from ./routes/ in routeTree.ts"
        source = (web_src / "routes" / f"{imports[name]}.tsx").read_text(
            encoding="utf-8"
        )
        definitions = _route_definitions(source)
        assert name in definitions, f"no `export const {name} = createRoute({{...}})`"
        body = definitions[name]
        assert _PARENT.findall(body) == ["rootRoute"], (
            f"{name}: parent is not rootRoute"
        )
        found = _PATH.findall(body)
        assert len(found) == 1, f"{name}: expected one literal path, found {found}"
        paths.append(found[0])
    return paths


def test_parser_reads_the_route_tree():
    paths = parse_client_routes(WEB_SRC)

    # Spot checks that the parser reads real data, not an empty match.
    assert "/" in paths
    assert "/jobs/$jobId" in paths
    assert "/uploads/new" in paths
    assert len(paths) == len(set(paths))


def test_client_routes_match_the_react_route_tree():
    react = parse_client_routes(WEB_SRC)

    missing = sorted(set(react) - set(CLIENT_ROUTES))
    extra = sorted(set(CLIENT_ROUTES) - set(react))
    assert missing == [], f"routes in routeTree.ts but not in CLIENT_ROUTES: {missing}"
    assert extra == [], (
        f"CLIENT_ROUTES entries the React router does not render: {extra}"
    )
    assert len(CLIENT_ROUTES) == len(set(CLIENT_ROUTES))


def test_every_client_route_compiles():
    for shape in CLIENT_ROUTES:
        compile_route(shape)


def test_parser_fails_on_a_nested_tree(tmp_path):
    (tmp_path / "routes").mkdir()
    (tmp_path / "routeTree.ts").write_text(
        "import { a } from './routes/a'\n"
        "export const routeTree = rootRoute.addChildren([a.addChildren([b])])\n"
    )

    with pytest.raises(AssertionError, match="nested"):
        parse_client_routes(tmp_path)


def test_parser_fails_on_a_computed_path(tmp_path):
    (tmp_path / "routes").mkdir()
    (tmp_path / "routeTree.ts").write_text(
        "import { a } from './routes/a'\nexport const routeTree = rootRoute.addChildren([a])\n"
    )
    (tmp_path / "routes" / "a.tsx").write_text(
        "export const a = createRoute({\n"
        "  getParentRoute: () => rootRoute,\n"
        "  path: PREFIX + '/a',\n"
        "})\n"
    )

    with pytest.raises(AssertionError, match="one literal path"):
        parse_client_routes(tmp_path)


def test_parser_reads_a_path_after_nested_objects(tmp_path):
    (tmp_path / "routes").mkdir()
    (tmp_path / "routeTree.ts").write_text(
        "import { a } from './routes/a.$id'\n"
        "export const routeTree = rootRoute.addChildren([\n  a,\n])\n"
    )
    (tmp_path / "routes" / "a.$id.tsx").write_text(
        "export const a = createRoute({\n"
        "  validateSearch: (s) => ({ q: String(s.q ?? ')') }),\n"
        "  getParentRoute: () => rootRoute,\n"
        "  path: '/a/$id',\n"
        "})\n"
    )

    assert parse_client_routes(tmp_path) == ["/a/$id"]


def _fixed_api_get_paths() -> set[str]:
    paths = create_app().openapi()["paths"]
    return {p for p, ops in paths.items() if "get" in ops and "{" not in p}


def test_fixed_api_paths_captured_by_a_client_route_are_api_only():
    """A fixed API GET path a ``$param`` would capture (``/clips/bulk-export.zip``
    as ``/clips/$clipId``) must be in ``API_ONLY_PATHS``, or an HTML request
    for it would get the SPA instead of the download."""
    patterns = [compile_route(shape) for shape in CLIENT_ROUTES]
    captured = {
        p for p in _fixed_api_get_paths() if any(pat.fullmatch(p) for pat in patterns)
    }

    assert captured - API_ONLY_PATHS == set()


def test_api_only_paths_are_real_api_paths():
    assert API_ONLY_PATHS <= _fixed_api_get_paths()
