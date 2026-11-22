"""Lorebook engine: keyword-triggered context entries for chat apps.

A slim take on SillyTavern World Info. Pure standard library, no LLM calls, no vectors.

Every turn:
  enabled -> constant (always in) -> timing (pinned / sticky in effect; cooldown blocks; delay not reached blocks)
  -> primary keys (case-insensitive; ASCII keys match whole words; CJK keys match substrings; /regex/flags supported)
  -> secondary keys by logic -> inclusion groups keep the highest order
  -> recursion (fired entries' text is rescanned, capped at N steps)
  -> budget (constants first, then higher order first; what doesn't fit is dropped and reported)
  -> one block: a header line, then each entry's content (names are for you, not for the model).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

LOGICS = ("and_any", "and_all", "not_any", "not_all")
CAUSES = ("user", "assistant", "recursion", "pin", "constant")
DEFAULT_HEADER = "【此刻挂着的设定】"


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


@dataclass
class Settings:
    scan_depth: int = 4          # how many recent messages to scan
    max_recursion: int = 3       # how many rescan steps
    budget_chars: int = 6000     # size cap of the injected block
    header: str = DEFAULT_HEADER

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(scan_depth=_env_int("LOREBOOK_SCAN_DEPTH", 4),
                   max_recursion=_env_int("LOREBOOK_MAX_RECURSION", 3),
                   budget_chars=_env_int("LOREBOOK_BUDGET_CHARS", 6000),
                   header=os.environ.get("LOREBOOK_HEADER", DEFAULT_HEADER))


@dataclass
class Entry:
    name: str
    content: str
    keys: list[str] = field(default_factory=list)
    keys2: list[str] = field(default_factory=list)
    logic: str = "and_any"
    constant: bool = False
    order: int = 100
    sticky: int = 0
    cooldown: int = 0
    delay: int = 0
    group: str = ""
    recursable: bool = True      # can be triggered by other entries' text
    recurse: bool = True         # its own text can trigger other entries
    enabled: bool = True
    source: str = ""
    id: int | None = None

    def normalized(self) -> "Entry":
        if self.logic not in LOGICS:
            self.logic = "and_any"
        self.keys = [str(k).strip() for k in (self.keys or []) if str(k).strip()]
        self.keys2 = [str(k).strip() for k in (self.keys2 or []) if str(k).strip()]
        self.name = (self.name or "").strip()
        self.group = (self.group or "").strip()
        self.sticky = max(0, int(self.sticky or 0))
        self.cooldown = max(0, int(self.cooldown or 0))
        self.delay = max(0, int(self.delay or 0))
        self.order = int(self.order if self.order is not None else 100)
        return self


def replace_names(text: str, user: str | None = None, char: str | None = None) -> str:
    """{{user}} / {{char}} -> the names you give. Names not given are left as placeholders."""
    if not text:
        return text or ""
    if user:
        text = re.sub(r"\{\{\s*user\s*\}\}", user, text, flags=re.IGNORECASE)
    if char:
        text = re.sub(r"\{\{\s*char\s*\}\}", char, text, flags=re.IGNORECASE)
    return text


# ── keyword matching ───────────────────────────────────────────────────────
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ가-힯]")
_REGEX_KEY = re.compile(r"^/(.+)/([a-z]*)$", re.DOTALL)
_cache: dict[str, re.Pattern[str] | None] = {}


def compile_key(key: str) -> re.Pattern[str] | None:
    key = (key or "").strip()
    if not key:
        return None
    if key in _cache:
        return _cache[key]
    m = _REGEX_KEY.match(key)
    try:
        if m:
            flags = re.IGNORECASE
            if "s" in m.group(2):
                flags |= re.DOTALL
            if "m" in m.group(2):
                flags |= re.MULTILINE
            pat: re.Pattern[str] | None = re.compile(m.group(1), flags)
        elif _CJK.search(key) or not key.isascii():
            pat = re.compile(re.escape(key), re.IGNORECASE)
        else:
            pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(key) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    except re.error:
        pat = None
    _cache[key] = pat
    return pat


def key_hits(keys: Iterable[str], text: str) -> list[str]:
    if not text:
        return []
    return [k for k in keys if (p := compile_key(k)) is not None and p.search(text)]


def secondary_ok(e: Entry, text: str) -> bool:
    if not e.keys2:
        return True
    n_hit, n_all = len(key_hits(e.keys2, text)), len(e.keys2)
    return {"and_any": n_hit > 0, "and_all": n_hit == n_all,
            "not_any": n_hit == 0, "not_all": n_hit < n_all}.get(e.logic, n_hit > 0)


# ── scan text ──────────────────────────────────────────────────────────────
def collect_scan_messages(messages: list[dict[str, Any]], *, skip_prefixes: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Chat messages ({role, content}) -> [(role, text)], oldest first. System messages are skipped.
    content may be a string or a list of blocks; only {"type": "text"} blocks are read.
    Paragraphs starting with one of skip_prefixes (e.g. your own injected blocks) are not scanned."""
    out: list[tuple[str, str]] = []
    for m in messages or []:
        role = str(m.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        c = m.get("content")
        if isinstance(c, list):
            t = "".join(str(b.get("text", "")) for b in c if isinstance(b, dict) and b.get("type") == "text")
        else:
            t = c if isinstance(c, str) else ""
        if skip_prefixes:
            t = "\n\n".join(p for p in t.split("\n\n") if not p.lstrip("\n").startswith(skip_prefixes))
        if t.strip():
            out.append((role, t))
    return out


# ── selection (pure) ───────────────────────────────────────────────────────
@dataclass
class Activation:
    entry: Entry
    cause: str
    via: str = "fresh"           # fresh = triggered this turn; sticky = still holding from before
    sticky_until: int = -1
    cooldown_until: int = -1


def _group_filter(acts: list[Activation]) -> list[Activation]:
    best: dict[str, Activation] = {}
    out: list[Activation] = []
    for a in acts:
        g = a.entry.group
        if not g:
            out.append(a)
            continue
        cur = best.get(g)
        if cur is None or (a.entry.order, -(a.entry.id or 0)) > (cur.entry.order, -(cur.entry.id or 0)):
            best[g] = a
    out.extend(best.values())
    out.sort(key=lambda a: (a.entry.order, a.entry.id or 0))
    return out


def select(entries: list[Entry], messages: list[tuple[str, str]], state: dict[int, dict[str, Any]],
           turn_no: int, settings: Settings | None = None) -> list[Activation]:
    """Entries + scan text + timing state + turn number -> what fires this turn (no budget, no writes)."""
    s = settings or Settings.from_env()
    window = messages[-s.scan_depth:] if s.scan_depth > 0 else []
    user_text = "\n".join(t for r, t in window if r == "user")
    asst_text = "\n".join(t for r, t in window if r == "assistant")
    all_text = "\n".join(t for _r, t in window)

    def blocked(e: Entry) -> bool:
        st = state.get(e.id or -1, {})
        if st.get("cooldown_until", -1) >= turn_no and st.get("sticky_until", -1) < turn_no:
            return True
        return e.delay > turn_no

    acts: list[Activation] = []
    for e in entries:
        if not e.enabled or e.id is None:
            continue
        st = state.get(e.id, {})
        if e.constant:
            acts.append(Activation(e, "constant"))
            continue
        if st.get("pinned"):
            acts.append(Activation(e, "pin"))
            continue
        if st.get("sticky_until", -1) >= turn_no:
            acts.append(Activation(e, st.get("last_cause") or "user", via="sticky",
                                   sticky_until=st.get("sticky_until", -1),
                                   cooldown_until=st.get("cooldown_until", -1)))
            continue
        if blocked(e) or not e.keys:
            continue
        cause = "user" if key_hits(e.keys, user_text) else ("assistant" if key_hits(e.keys, asst_text) else "")
        if cause and secondary_ok(e, all_text):
            acts.append(Activation(e, cause))

    acts = _group_filter(acts)
    taken = {a.entry.id for a in acts}
    frontier, steps = list(acts), 0
    while frontier and steps < s.max_recursion:
        steps += 1
        rec_text = "\n".join(a.entry.content for a in frontier if a.entry.recurse)
        if not rec_text.strip():
            break
        new = [Activation(e, "recursion") for e in entries
               if e.enabled and e.id is not None and e.id not in taken and e.recursable and not e.constant
               and not blocked(e) and e.keys and key_hits(e.keys, rec_text)
               and secondary_ok(e, all_text + "\n" + rec_text)]
        if not new:
            break
        before = {a.entry.id for a in acts}
        acts = _group_filter(acts + new)
        after = {a.entry.id for a in acts}
        taken |= after | {a.entry.id for a in new}
        frontier = [a for a in acts if a.entry.id in (after - before)]
    return acts


def apply_budget(acts: list[Activation], settings: Settings | None = None) -> tuple[list[Activation], list[str]]:
    """Constants first, then higher order first. Returns (kept in order, names dropped)."""
    s = settings or Settings.from_env()
    ranked = sorted(acts, key=lambda a: (0 if a.entry.constant else 1, -a.entry.order, a.entry.id or 0))
    kept, dropped, used = [], [], len(s.header) + 1
    for a in ranked:
        n = len(a.entry.content) + 2
        if used + n > s.budget_chars:
            dropped.append(a.entry.name)
            continue
        used += n
        kept.append(a)
    kept.sort(key=lambda a: (a.entry.order, a.entry.id or 0))
    return kept, dropped


def render(acts: list[Activation], settings: Settings | None = None) -> str:
    s = settings or Settings.from_env()
    bodies = [a.entry.content.strip() for a in acts if a.entry.content.strip()]
    return (s.header + "\n" + "\n\n".join(bodies)) if bodies else ""


# ── storage ────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    keys TEXT NOT NULL DEFAULT '[]', keys2 TEXT NOT NULL DEFAULT '[]', logic TEXT NOT NULL DEFAULT 'and_any',
    constant INTEGER NOT NULL DEFAULT 0, "order" INTEGER NOT NULL DEFAULT 100,
    sticky INTEGER NOT NULL DEFAULT 0, cooldown INTEGER NOT NULL DEFAULT 0, delay INTEGER NOT NULL DEFAULT 0,
    group_name TEXT NOT NULL DEFAULT '', recursable INTEGER NOT NULL DEFAULT 1, recurse INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1, content TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lorebook_state (
    entry_id INTEGER PRIMARY KEY, sticky_until INTEGER NOT NULL DEFAULT -1, cooldown_until INTEGER NOT NULL DEFAULT -1,
    pinned INTEGER NOT NULL DEFAULT 0, last_cause TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lorebook_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lorebook_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, turn_no INTEGER NOT NULL, turn_id TEXT, ts TEXT NOT NULL,
    entry_id INTEGER NOT NULL, name TEXT NOT NULL, cause TEXT NOT NULL, via TEXT NOT NULL DEFAULT 'fresh',
    sticky_until INTEGER NOT NULL DEFAULT -1, cooldown_until INTEGER NOT NULL DEFAULT -1
);
CREATE INDEX IF NOT EXISTS idx_lorebook_turns_turn ON lorebook_turns(turn_no);
"""


class Lorebook:
    """One SQLite file: entries, their timing state, the turn counter and a log of what fired."""

    def __init__(self, path: str | os.PathLike, settings: Settings | None = None) -> None:
        self.path = Path(path)
        self.settings = settings or Settings.from_env()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=5)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _row(r: sqlite3.Row) -> Entry:
        return Entry(id=int(r["id"]), name=r["name"], content=r["content"],
                     keys=json.loads(r["keys"] or "[]"), keys2=json.loads(r["keys2"] or "[]"),
                     logic=r["logic"], constant=bool(r["constant"]), order=int(r["order"]),
                     sticky=int(r["sticky"]), cooldown=int(r["cooldown"]), delay=int(r["delay"]),
                     group=r["group_name"], recursable=bool(r["recursable"]), recurse=bool(r["recurse"]),
                     enabled=bool(r["enabled"]), source=r["source"]).normalized()

    def upsert(self, e: Entry) -> int:
        """Insert or overwrite by name. The id stays stable so timing state survives re-imports."""
        e = e.normalized()
        if not e.name:
            raise ValueError("entry has no name")
        vals = (json.dumps(e.keys, ensure_ascii=False), json.dumps(e.keys2, ensure_ascii=False), e.logic,
                int(e.constant), e.order, e.sticky, e.cooldown, e.delay, e.group, int(e.recursable),
                int(e.recurse), int(e.enabled), e.content, e.source)
        with self._conn() as c:
            cur = c.execute("SELECT id FROM entries WHERE name=?", (e.name,)).fetchone()
            if cur is None:
                c.execute('INSERT INTO entries (keys, keys2, logic, constant, "order", sticky, cooldown, delay, '
                          'group_name, recursable, recurse, enabled, content, source, name) '
                          'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (*vals, e.name))
            else:
                c.execute('UPDATE entries SET keys=?, keys2=?, logic=?, constant=?, "order"=?, sticky=?, cooldown=?, '
                          'delay=?, group_name=?, recursable=?, recurse=?, enabled=?, content=?, source=?, '
                          "updated_at=datetime('now') WHERE id=?", (*vals, int(cur["id"])))
            return int(c.execute("SELECT id FROM entries WHERE name=?", (e.name,)).fetchone()["id"])

    def entries(self, only_enabled: bool = False) -> list[Entry]:
        sql = "SELECT * FROM entries" + (" WHERE enabled=1" if only_enabled else "") + ' ORDER BY "order", id'
        with self._conn() as c:
            return [self._row(r) for r in c.execute(sql).fetchall()]

    def get(self, name: str) -> Entry | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM entries WHERE name=?", (name,)).fetchone()
            return self._row(r) if r else None

    def delete(self, name: str) -> bool:
        with self._conn() as c:
            r = c.execute("SELECT id FROM entries WHERE name=?", (name,)).fetchone()
            if r is None:
                return False
            c.execute("DELETE FROM lorebook_state WHERE entry_id=?", (r["id"],))
            c.execute("DELETE FROM entries WHERE id=?", (r["id"],))
            return True

    def set_enabled(self, name: str, on: bool) -> bool:
        with self._conn() as c:
            return c.execute("UPDATE entries SET enabled=?, updated_at=datetime('now') WHERE name=?",
                             (int(on), name)).rowcount > 0

    def pin(self, name: str) -> bool:
        """Pinned = sticky until unpinned."""
        e = self.get(name)
        if e is None or not e.enabled:
            return False
        with self._conn() as c:
            c.execute("INSERT INTO lorebook_state (entry_id, pinned, last_cause) VALUES (?,1,'pin') "
                      "ON CONFLICT(entry_id) DO UPDATE SET pinned=1, last_cause='pin', updated_at=datetime('now')",
                      (e.id,))
        return True

    def unpin(self, name: str) -> bool:
        e = self.get(name)
        if e is None:
            return False
        with self._conn() as c:
            c.execute("INSERT INTO lorebook_state (entry_id, pinned, sticky_until) VALUES (?,0,-1) "
                      "ON CONFLICT(entry_id) DO UPDATE SET pinned=0, sticky_until=-1, updated_at=datetime('now')",
                      (e.id,))
        return True

    def _state(self, c: sqlite3.Connection) -> dict[int, dict[str, Any]]:
        return {int(r["entry_id"]): {"sticky_until": int(r["sticky_until"]), "cooldown_until": int(r["cooldown_until"]),
                                     "pinned": bool(r["pinned"]), "last_cause": r["last_cause"]}
                for r in c.execute("SELECT * FROM lorebook_state").fetchall()}

    def _meta(self, c: sqlite3.Connection, key: str, default: str = "") -> str:
        r = c.execute("SELECT value FROM lorebook_meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def preview(self, messages: list[tuple[str, str]]) -> tuple[str, list[Activation], list[str]]:
        """What would fire next turn, without touching any state."""
        with self._conn() as c:
            turn_no = int(self._meta(c, "turn_no", "0") or 0) + 1
            acts = select(self.entries(only_enabled=True), messages, self._state(c), turn_no, self.settings)
        kept, dropped = apply_budget(acts, self.settings)
        return render(kept, self.settings), kept, dropped

    def build_for_turn(self, messages: list[tuple[str, str]], turn_id: str | int | None = None
                       ) -> tuple[str, dict[str, Any]]:
        """One turn: select -> budget -> record timing and log -> (block, report).

        Pass turn_id (e.g. the id of the user message) so that a regenerate of the same turn replays:
        no new turn number, no new timing, same result."""
        report: dict[str, Any] = {"fired": [], "dropped": [], "turn_no": None, "replay": False}
        entries = self.entries(only_enabled=True)
        if not entries:
            return "", report
        with self._conn() as c:
            turn_no = int(self._meta(c, "turn_no", "0") or 0)
            last = self._meta(c, "last_turn_id", "")
            replay = turn_id is not None and last != "" and str(turn_id) == last
            if not replay:
                turn_no += 1
            report.update(turn_no=turn_no, replay=replay)
            acts = select(entries, messages, self._state(c), turn_no, self.settings)
            kept, dropped = apply_budget(acts, self.settings)
            report["dropped"] = dropped
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for a in kept:
                e = a.entry
                if a.via == "fresh" and a.cause not in ("constant", "pin"):
                    a.sticky_until = turn_no + e.sticky
                    a.cooldown_until = a.sticky_until + e.cooldown
                    if not replay:
                        c.execute("INSERT INTO lorebook_state (entry_id, sticky_until, cooldown_until, last_cause) "
                                  "VALUES (?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET "
                                  "sticky_until=excluded.sticky_until, cooldown_until=excluded.cooldown_until, "
                                  "last_cause=excluded.last_cause, updated_at=datetime('now')",
                                  (e.id, a.sticky_until, a.cooldown_until, a.cause))
                if not replay:
                    c.execute("INSERT INTO lorebook_turns (turn_no, turn_id, ts, entry_id, name, cause, via, "
                              "sticky_until, cooldown_until) VALUES (?,?,?,?,?,?,?,?,?)",
                              (turn_no, None if turn_id is None else str(turn_id), ts, e.id, e.name,
                               a.cause, a.via, a.sticky_until, a.cooldown_until))
                report["fired"].append({"name": e.name, "cause": a.cause, "via": a.via})
            if not replay:
                c.execute("INSERT INTO lorebook_meta (key, value) VALUES ('turn_no', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(turn_no),))
                if turn_id is not None:
                    c.execute("INSERT INTO lorebook_meta (key, value) VALUES ('last_turn_id', ?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(turn_id),))
        block = render(kept, self.settings)
        report["chars"] = len(block)
        return block, report

    def log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM lorebook_turns ORDER BY id DESC LIMIT ?",
                                               (int(limit),)).fetchall()]
