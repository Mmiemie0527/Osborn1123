import json
from pathlib import Path

from florilegium import Entry, Lorebook, Settings, key_hits, select
from florilegium import folder as F
from florilegium.__main__ import main as cli
from florilegium.importers import from_sillytavern, to_sillytavern

S = Settings(scan_depth=4, max_recursion=3, budget_chars=6000, header="[lore]")


def book(tmp_path, *entries):
    b = Lorebook(tmp_path / "lb.db", S)
    for e in entries:
        b.upsert(e)
    return b


def test_keys_cjk_substring_ascii_whole_word_regex():
    assert key_hits(["下雨"], "外面在下雨呢") == ["下雨"]
    assert key_hits(["cat"], "a category") == []
    assert key_hits(["cat"], "the Cat sat") == ["cat"]
    assert key_hits(["/rain(y|ing)/"], "it is raining") == ["/rain(y|ing)/"]
    assert key_hits(["/([/"], "anything") == []          # a broken regex is ignored, not fatal


def test_sticky_then_cooldown(tmp_path):
    b = book(tmp_path, Entry(name="rain", content="R", keys=["rain"], sticky=2, cooldown=1))
    fired = []
    for line in ["rain", "x", "x", "x", "rain", "rain"]:
        _, r = b.build_for_turn([("user", line)])
        fired.append([f["name"] for f in r["fired"]])
    # turn1 fires, holds through turn3, cooldown turn4, turn5 fires again
    assert fired == [["rain"], ["rain"], ["rain"], [], ["rain"], ["rain"]]


def test_recursion_and_its_switches(tmp_path):
    b = book(tmp_path,
             Entry(name="a", content="this mentions storm", keys=["rain"]),
             Entry(name="b", content="B", keys=["storm"]),
             Entry(name="c", content="C", keys=["storm"], recursable=False))
    block, r = b.build_for_turn([("user", "rain")])
    names = {f["name"]: f["cause"] for f in r["fired"]}
    assert names == {"a": "user", "b": "recursion"}


def test_group_keeps_highest_order(tmp_path):
    b = book(tmp_path, Entry(name="low", content="L", keys=["x"], group="g", order=1),
             Entry(name="high", content="H", keys=["x"], group="g", order=9))
    _, r = b.build_for_turn([("user", "x")])
    assert [f["name"] for f in r["fired"]] == ["high"]


def test_budget_constants_first_then_order(tmp_path):
    s = Settings(budget_chars=32, header="[lore]")   # 7 + 12 + 12 = 31 fits, the third does not
    b = Lorebook(tmp_path / "lb.db", s)
    b.upsert(Entry(name="always", content="A" * 10, constant=True, order=1))
    b.upsert(Entry(name="big", content="B" * 10, keys=["x"], order=50))
    b.upsert(Entry(name="small", content="C" * 10, keys=["x"], order=5))
    block, r = b.build_for_turn([("user", "x")])
    assert [f["name"] for f in r["fired"]] == ["always", "big"] and r["dropped"] == ["small"]
    assert block.startswith("[lore]\n")


def test_secondary_logic(tmp_path):
    b = book(tmp_path, Entry(name="n", content="N", keys=["rain"], keys2=["inside"], logic="not_any"))
    assert book(tmp_path).preview([("user", "rain inside")])[1] == []
    assert [k.entry.name for k in b.preview([("user", "rain outside")])[1]] == ["n"]


def test_assistant_text_also_triggers_and_cause_is_marked(tmp_path):
    b = book(tmp_path, Entry(name="r", content="R", keys=["rain"]))
    _, r = b.build_for_turn([("user", "hi"), ("assistant", "it might rain")])
    assert r["fired"] == [{"name": "r", "cause": "assistant", "via": "fresh"}]


def test_regenerate_same_turn_is_a_replay(tmp_path):
    b = book(tmp_path, Entry(name="r", content="R", keys=["rain"], sticky=1))
    _, r1 = b.build_for_turn([("user", "rain")], turn_id=7)
    _, r2 = b.build_for_turn([("user", "rain")], turn_id=7)
    assert r1["turn_no"] == r2["turn_no"] == 1 and r2["replay"] is True
    assert len(b.log()) == 1


def test_pin_holds_until_unpinned(tmp_path):
    b = book(tmp_path, Entry(name="r", content="R", keys=["rain"]))
    assert b.pin("r")
    assert [f["name"] for f in b.build_for_turn([("user", "nothing")])[1]["fired"]] == ["r"]
    b.unpin("r")
    assert b.build_for_turn([("user", "nothing")])[1]["fired"] == []


def test_folder_roundtrip_both_languages(tmp_path):
    e = Entry(name="雨天", content="正文第一段。\n\n第二段。", keys=["下雨", "rain"], keys2=["窗"],
              logic="and_all", sticky=2, cooldown=1, delay=3, order=80, group="天气", recursable=False)
    for english in (False, True):
        d = tmp_path / ("en" if english else "zh")
        F.export([e], d, english=english)
        back = F.parse(d / "雨天.txt")
        for k in ("name", "content", "keys", "keys2", "logic", "sticky", "cooldown", "delay", "order",
                  "group", "recursable", "recurse", "constant", "enabled"):
            assert getattr(back, k) == getattr(e, k), (english, k)
    assert F.check(tmp_path / "zh") == []


def test_folder_check_catches_problems(tmp_path):
    (tmp_path / "a.txt").write_text("触发词：x\n\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("触发词：x\n\nbody", encoding="utf-8")
    (tmp_path / "c.txt").write_text("常驻：否\n\nbody", encoding="utf-8")
    (tmp_path / "_notes.txt").write_text("not an entry", encoding="utf-8")
    probs = " | ".join(F.check(tmp_path))
    assert "empty body" in probs and "'x' is in both" in probs and "never fire" in probs


def test_sillytavern_mapping_and_back():
    st = {"entries": {"0": {"uid": 0, "comment": "R {{char}}", "key": ["rain"], "keysecondary": ["in"],
                            "selectiveLogic": 2, "constant": False, "order": 40, "sticky": 2, "cooldown": 1,
                            "delay": 0, "group": "g", "excludeRecursion": True, "preventRecursion": False,
                            "disable": False, "content": "{{user}} hears rain"}}}
    [e] = from_sillytavern(st, user="U", char="C")
    assert (e.name, e.content, e.keys2, e.logic, e.recursable) == ("R C", "U hears rain", ["in"], "not_any", False)
    [e2] = from_sillytavern(st)
    assert e2.content == "{{user}} hears rain"            # no names given = placeholders kept
    assert from_sillytavern(to_sillytavern([e]))[0].logic == "not_any"


def test_cli_import_preview(tmp_path, capsys):
    d = tmp_path / "f"
    F.export([Entry(name="rain", content="It is raining.", keys=["rain"])], d)
    db = str(tmp_path / "lb.db")
    assert cli(["import", db, str(d)]) == 0 and not Path(db).exists() or Lorebook(db).entries() == []
    assert cli(["import", db, str(d), "--apply"]) == 0
    assert cli(["preview", db, "I love rain"]) == 0
    out = capsys.readouterr().out
    assert "+ rain" in out and "It is raining." in out
    j = tmp_path / "out.json"
    assert cli(["tojson", str(d), str(j)]) == 0 and json.loads(j.read_text())["entries"]["0"]["key"] == ["rain"]
