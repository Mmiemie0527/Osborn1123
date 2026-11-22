"""Read SillyTavern World Info / character book JSON into entries.

Field mapping: key->keys, keysecondary->keys2, selectiveLogic (0 and_any / 1 not_all / 2 not_any / 3 and_all)->logic,
constant, order, sticky, cooldown, delay, group, excludeRecursion->recursable=False,
preventRecursion->recurse=False, disable->enabled=False, comment->name, content.
{{user}} / {{char}} are replaced only if you pass names.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .engine import LOGICS, Entry, replace_names

_ST_LOGIC = {0: "and_any", 1: "not_all", 2: "not_any", 3: "and_all"}


def _s(x: Any) -> str:
    return x.strip() if isinstance(x, str) else ""


def _int(x: Any, default: int = 0) -> int:
    try:
        return int(x) if x is not None and x != "" else default
    except (TypeError, ValueError):
        return default


def _keys(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(i).strip() for i in v if str(i).strip()]
    if isinstance(v, str) and v.strip():
        return [p.strip() for p in re.split(r"[,，、]", v) if p.strip()]
    return []


def _items(d: dict) -> list[dict]:
    entries = d.get("entries")
    if entries is None and isinstance(d.get("data"), dict) and isinstance(d["data"].get("character_book"), dict):
        entries = d["data"]["character_book"].get("entries")
    if entries is None and isinstance(d.get("character_book"), dict):
        entries = d["character_book"].get("entries")
    if isinstance(entries, dict):
        vals = [v for v in entries.values() if isinstance(v, dict)]
        return sorted(vals, key=lambda v: v.get("uid") if isinstance(v.get("uid"), (int, float)) else 10**9)
    if isinstance(entries, list):
        return [v for v in entries if isinstance(v, dict)]
    return []


def from_sillytavern(d: dict, *, user: str | None = None, char: str | None = None, source: str = "") -> list[Entry]:
    out: list[Entry] = []
    seen: dict[str, int] = {}
    for i, e in enumerate(_items(d), 1):
        content = _s(e.get("content"))
        if not content:
            continue
        keys = _keys(e.get("key") if e.get("key") is not None else e.get("keys"))
        keys2 = _keys(e.get("keysecondary") if e.get("keysecondary") is not None else e.get("secondary_keys"))
        name = _s(e.get("comment")) or _s(e.get("name")) or (", ".join(keys[:3]) if keys else f"entry {i}")
        name = replace_names(name, user, char)
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name} ·{seen[name]}"
        raw = e.get("selectiveLogic")
        logic = raw if isinstance(raw, str) and raw in LOGICS else _ST_LOGIC.get(_int(raw, 0), "and_any")
        out.append(Entry(
            name=name, content=replace_names(content, user, char),
            keys=[replace_names(k, user, char) for k in keys], keys2=[replace_names(k, user, char) for k in keys2],
            logic=logic, constant=e.get("constant") is True, order=_int(e.get("order"), 100),
            sticky=_int(e.get("sticky")), cooldown=_int(e.get("cooldown")), delay=_int(e.get("delay")),
            group=_s(e.get("group")), recursable=e.get("excludeRecursion") is not True,
            recurse=e.get("preventRecursion") is not True,
            enabled=not (e.get("disable") is True or e.get("enabled") is False), source=source,
        ).normalized())
    return out


def to_sillytavern(entries: list[Entry]) -> dict[str, Any]:
    """Back out to a SillyTavern-shaped world book (the fields this engine understands)."""
    rev = {v: k for k, v in _ST_LOGIC.items()}
    return {"entries": {str(i): {
        "uid": i, "comment": e.name, "key": e.keys, "keysecondary": e.keys2,
        "selectiveLogic": rev.get(e.logic, 0), "constant": e.constant, "order": e.order,
        "sticky": e.sticky, "cooldown": e.cooldown, "delay": e.delay, "group": e.group,
        "excludeRecursion": not e.recursable, "preventRecursion": not e.recurse,
        "disable": not e.enabled, "content": e.content} for i, e in enumerate(entries)}}


def read_json(path: Path, *, user: str | None = None, char: str | None = None) -> list[Entry]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError(f"{path}: not a JSON object")
    return from_sillytavern(d, user=user, char=char, source=Path(path).name)
