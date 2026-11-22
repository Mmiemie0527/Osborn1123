"""Folder format: one entry per .txt file, editable in any text editor.

    触发词：rain, 下雨, umbrella        (or  keys: ...)
    粘几轮：2                            (or  sticky: ...)
    冷却几轮：0                          (or  cooldown: ...)
    排序：100                            (or  order: ...)
    常驻：否                             (or  constant: no)
    开关：开                             (or  enabled: yes)

    The body starts after the first blank line.

File name without .txt = entry name. Subfolders are only for your own sorting; they are ignored.
Files whose names start with "_" or "." are skipped (put notes there).
Optional head lines: 副触发词 / keys2, 副词逻辑 / logic, 延迟几轮 / delay, 包容组 / group,
不被别的条目点着 / exclude_recursion, 不去点别的条目 / prevent_recursion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import LOGICS, Entry

YES = {"是", "开", "对", "要", "yes", "y", "true", "on", "1"}
HEAD: dict[str, str] = {
    "触发词": "keys", "keys": "keys",
    "副触发词": "keys2", "keys2": "keys2",
    "副词逻辑": "logic", "logic": "logic",
    "粘几轮": "sticky", "sticky": "sticky",
    "冷却几轮": "cooldown", "cooldown": "cooldown",
    "延迟几轮": "delay", "delay": "delay",
    "排序": "order", "order": "order",
    "常驻": "constant", "constant": "constant",
    "开关": "enabled", "enabled": "enabled",
    "包容组": "group", "group": "group",
    "不被别的条目点着": "exclude_recursion", "exclude_recursion": "exclude_recursion",
    "不去点别的条目": "prevent_recursion", "prevent_recursion": "prevent_recursion",
}


def split_keys(s: str) -> list[str]:
    for sep in ("，", ",", "／", "；", ";"):
        s = s.replace(sep, "、")
    return [k.strip() for k in s.split("、") if k.strip()]


def render(e: Entry, english: bool = False) -> str:
    yn = (lambda b: "yes" if b else "no") if english else (lambda b: "是" if b else "否")
    onoff = (lambda b: "yes" if b else "no") if english else (lambda b: "开" if b else "关")
    sep = ", " if english else "、"
    lines = [f"keys: {sep.join(e.keys)}" if english else f"触发词：{sep.join(e.keys)}"]
    rows: list[tuple[str, str, Any]] = [
        ("副触发词", "keys2", sep.join(e.keys2) if e.keys2 else None),
        ("副词逻辑", "logic", e.logic if e.keys2 else None),
        ("粘几轮", "sticky", e.sticky), ("冷却几轮", "cooldown", e.cooldown),
        ("延迟几轮", "delay", e.delay or None), ("排序", "order", e.order),
        ("常驻", "constant", yn(e.constant)), ("开关", "enabled", onoff(e.enabled)),
        ("包容组", "group", e.group or None),
        ("不被别的条目点着", "exclude_recursion", yn(not e.recursable)),
        ("不去点别的条目", "prevent_recursion", yn(not e.recurse) if not e.recurse else None),
    ]
    for zh, en, v in rows:
        if v is None:
            continue
        lines.append(f"{en}: {v}" if english else f"{zh}：{v}")
    return "\n".join(lines) + "\n\n" + (e.content or "").strip() + "\n"


def parse(path: Path) -> Entry:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    head, _, body = text.partition("\n\n")
    e = Entry(name=path.stem, content=body.strip())
    for ln in head.split("\n"):
        if not ln.strip():
            continue
        k, sep, v = ln.partition("：")
        if not sep:
            k, sep, v = ln.partition(":")
        k, v = k.strip(), v.strip()
        f = HEAD.get(k) or HEAD.get(k.lower())
        if not f:
            raise ValueError(f"{path.name}: unknown head line {ln!r} (leave a blank line between head and body)")
        if f in ("keys", "keys2"):
            setattr(e, f, split_keys(v))
        elif f in ("sticky", "cooldown", "delay", "order"):
            setattr(e, f, int(v or 0))
        elif f == "logic":
            e.logic = v if v in LOGICS else "and_any"
        elif f == "constant":
            e.constant = v.lower() in YES
        elif f == "enabled":
            e.enabled = v.lower() in YES
        elif f == "group":
            e.group = v
        elif f == "exclude_recursion":
            e.recursable = v.lower() not in YES
        elif f == "prevent_recursion":
            e.recurse = v.lower() not in YES
    e.source = str(path.parent.name)
    return e.normalized()


def walk(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).rglob("*.txt") if not p.name.startswith(("_", ".")))


def check(folder: Path) -> list[str]:
    """Duplicate names, a key used by two entries, empty bodies, entries that can never fire, bad heads."""
    probs: list[str] = []
    names: dict[str, Path] = {}
    keys: dict[str, str] = {}
    for p in walk(folder):
        try:
            e = parse(p)
        except ValueError as ex:
            probs.append(str(ex))
            continue
        if e.name in names:
            probs.append(f"duplicate name {e.name!r}: {names[e.name]} and {p}")
        names[e.name] = p
        if not e.content:
            probs.append(f"{e.name!r}: empty body")
        if not e.keys and not e.constant:
            probs.append(f"{e.name!r}: no keys and not constant, it can never fire")
        for k in e.keys:
            if k in keys and keys[k] != e.name:
                probs.append(f"key {k!r} is in both {keys[k]!r} and {e.name!r}")
            keys.setdefault(k, e.name)
    return probs


def load(folder: Path) -> list[Entry]:
    return [parse(p) for p in walk(folder)]


def export(entries: list[Entry], folder: Path, *, english: bool = False, force: bool = False) -> int:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for e in entries:
        p = folder / f"{e.name}.txt"
        if p.exists() and not force:
            continue
        p.write_text(render(e, english=english), encoding="utf-8")
        n += 1
    return n
