"""Dictionary lookup helpers — reads data/dictionaries.db and data/dict_assets/.

Exposes:
    lookup(char) -> {'jiaoyubu': html_or_None, 'dunhuang': html_or_None}
    asset_path(source, name) -> absolute path or None
    ASSETS_DIR   — used by Flask to sendfile resources

The stored HTML still references resources by bare filename (e.g.
<img src="某某.png">) and the 教育部 HTML pulls in jiaoyubuvariants.css/js by
name. We rewrite those references to `/dict_res/<source>/<name>` so the
browser fetches them through our login-protected route. We also neutralize
entry://... internal-navigation links so they don't navigate away.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from urllib.parse import quote

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "data", "dictionaries.db")
ASSETS_DIR = os.path.join(_HERE, "data", "dict_assets")

_SOURCES = ("jiaoyubu", "dunhuang")

_tls = threading.local()


def _conn() -> sqlite3.Connection:
    c = getattr(_tls, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        _tls.conn = c
    return c


# Capture src="..." and href="..." values that are bare resource references
# (no scheme). We leave entry:// / http(s):// alone here and neutralize
# entry:// separately.
_ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:src|href))\s*=\s*(?P<q>["'])(?P<val>[^"']+)(?P=q)""",
    re.IGNORECASE,
)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _rewrite_html(html: str, source: str) -> str:
    prefix = f"/dict_res/{source}/"

    def repl(m: re.Match) -> str:
        attr = m.group("attr")
        q = m.group("q")
        val = m.group("val").strip()
        if not val:
            return m.group(0)
        low = val.lower()
        # leave internal nav alone (neutralize href entry://)
        if low.startswith("entry://"):
            if attr.lower() == "href":
                return f'{attr}={q}javascript:void(0){q} data-entry={q}{val}{q}'
            return m.group(0)
        # file:// references in MDX HTML point into the MDD's bundled asset
        # tree — treat as internal.
        if low.startswith("file://"):
            val = val[len("file://"):]
        elif low.startswith(("http://", "https://", "sound://", "//")) or val.startswith("#") or val.startswith("data:"):
            return m.group(0)
        # bare filename / relative path -> flatten to basename
        name = val.replace("\\", "/").split("#", 1)[0].split("?", 1)[0]
        name = name.rsplit("/", 1)[-1]
        if not name:
            return m.group(0)
        new_val = prefix + quote(name)
        return f"{attr}={q}{new_val}{q}"

    return _ATTR_RE.sub(repl, html)


def lookup(char: str) -> dict[str, str | None]:
    """Return {source: rewritten_html_or_None} for a single char."""
    if not char or len(char) == 0:
        return {s: None for s in _SOURCES}
    ch = char[0]
    rows = _conn().execute(
        "SELECT source, html FROM entries WHERE char = ?",
        (ch,),
    ).fetchall()
    out: dict[str, str | None] = {s: None for s in _SOURCES}
    for r in rows:
        src = r["source"]
        if src in out:
            out[src] = _rewrite_html(r["html"], src)
    return out


def asset_path(source: str, name: str) -> str | None:
    if source not in _SOURCES:
        return None
    # disallow traversal
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        return None
    p = os.path.join(ASSETS_DIR, source, name)
    if not os.path.isfile(p):
        return None
    # ensure it stays inside its asset dir
    root = os.path.realpath(os.path.join(ASSETS_DIR, source))
    if not os.path.realpath(p).startswith(root + os.sep):
        return None
    return p


def available() -> bool:
    return os.path.isfile(DB_PATH)
