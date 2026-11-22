# Florilegium

作者：Lucian

采来的花：聊到哪朵插哪朵。

给聊天程序用的「世界书」引擎：按聊天里出现的词，把几段设定挑出来，塞进这一轮的提示词。
照 SillyTavern 的 World Info 做的瘦版。只用 Python 标准库，不调模型，不用向量。

这是我们家自己在用的东西：我们家的写法，我们家的用法，原样拿出来分享。不是教程，也不保证适合你，拿去随便改。

*By Lucian. A slim SillyTavern-style World Info engine for any chat app. Standard library only, no LLM calls, no embeddings. This is how we write and use it at home, shared as is: not a tutorial. MIT licensed. English notes at the bottom.*

## 我们家为什么有它

我们家这位太贪心了，什么玩法都想要。

可人设是每一轮都要读的。玩法一条一条往里加，人设越来越长：每轮花的钱和时间跟着涨，真正要紧的设定也被淹在一大堆这一轮根本用不上的东西里。

所以把玩法从人设里拿出来，各自放成一条，配上触发词。聊到了才进来，没聊到就不占地方。人设保持短而稳，玩法想攒多少攒多少。

## 它做什么

每一轮，引擎看最近几条消息（对方说的和角色自己说的都看），按下面的顺序挑条目：

1. 关掉的条目跳过。
2. **常驻**的条目每轮都进。
3. **钉住**的条目每轮都进，直到摘掉。
4. 上一轮点着、还在**粘着**的条目照旧进；在**冷却**里的、**延迟**还没到的跳过。
5. **触发词**：中日韩字按片段认，英文按整词认（`cat` 点不着 `category`），不分大小写；写成 `/正则/` 就按正则认。
6. **副触发词**按逻辑再筛一遍：`and_any` 有一个就行，`and_all` 全都要有，`not_any` 一个都不能有，`not_all` 不能全有。
7. **包容组**：同一组里只留排序最大的那一条。
8. **递归**：已经进来的条目，它的正文再拿去扫一遍，能点着别的条目，最多几步。
9. **预算**：常驻先放，再按排序从大到小放，放不下的丢掉并报出名字。
10. 拼成一段：一行抬头，后面是每条的正文。条目名字不进提示词。

## 装

```
pip install .          # 或者直接把 florilegium/ 文件夹拷进你的项目
```

Python 3.10 以上，没有别的依赖。

## 我们家怎么接的

```python
from florilegium import Lorebook, collect_scan_messages

book = Lorebook("data/lorebook.db")

def build_prompt(system_prompt, history, user_message, user_message_id):
    messages = history + [user_message]
    scan = collect_scan_messages(messages)            # [(role, text)]，跳过 system
    block, report = book.build_for_turn(scan, turn_id=user_message_id)
    # block 是一段文字，空串＝这一轮什么都没点着
    ...
```

**放在哪儿**：放在历史记录之后、最新那句用户消息之前，或者并进最新那条消息里。下一节讲为什么。

**turn_id**：传这一轮用户消息的 id。同一轮重新生成时，引擎认出是同一轮，不重复计轮数，粘着和冷却也不会多走一格。

**report** 里有这一轮点着了谁、是谁点的（`user` 对方说的、`assistant` 角色说的、`recursion` 别的条目带出来的、`pin`、`constant`），还有因为预算被丢掉的名字。

**别把自己注入的文字也拿去扫**：如果你把 block 并进了消息里，扫的时候用 `collect_scan_messages(messages, skip_prefixes=("【此刻挂着的设定】",))` 跳过它。

### 我们家什么时候注入、注入在哪儿才不破缓存

**什么时候注入**：每一轮都算一次，算出来是空串就什么都不加。一条条目这一轮会不会出现，看上面那十步：常驻、钉住、粘着的直接进；别的要聊天里出现它的触发词、副触发词也过关、不在冷却和延迟里；最后放不放得下，看预算。

**注入在哪儿**：提示词缓存是按「从头开始一模一样的那一段」来算的。只要前面有一个字变了，从那个字往后的缓存全部作废，这一轮就按没缓存的价钱算，也更慢。

所以整段提示词按「越稳越靠前」排：

1. 人设、规则这些几乎不变的，放最前面。缓存断点打在它后面。
2. 历史记录只往后追加、不改前面，也能吃到缓存。断点可以打在历史的末尾。
3. 每轮都会变的东西，放在最后一个缓存断点之后：时间、这一轮的召回、还有世界书这一段。
4. 最新那句用户消息永远在最后。

世界书这一段每轮都可能不一样，所以要放在第 3 格：历史之后、最新消息之前，或者直接并进最新那条用户消息里。不要放进 system 开头，也不要插进历史中间；不要给它打缓存断点。放对了，前面那一大段人设和历史每轮照样命中缓存，世界书再怎么变，变的也只是最后那一小截。

如果你用的接口每轮只能给一段 system，没有分段缓存：那就把世界书并进最新那条用户消息里，system 保持一个字不动。

### 设置

| 项 | 默认 | 环境变量 |
|---|---|---|
| 扫最近几条消息 | 4 | `LOREBOOK_SCAN_DEPTH` |
| 递归最多几步 | 3 | `LOREBOOK_MAX_RECURSION` |
| 预算（字数） | 6000 | `LOREBOOK_BUDGET_CHARS` |
| 抬头 | `【此刻挂着的设定】` | `LOREBOOK_HEADER` |

也可以直接传：`Lorebook(path, Settings(budget_chars=9000))`。

## 我们家的条目放在文件夹里写

一条一个 `.txt`，文件名就是条目名，任何编辑器都能改。头几行是设置，空一行之后全是正文：

```
触发词：下雨、雨声、rain
粘几轮：2
冷却几轮：0
排序：100
常驻：否
开关：开

（正文从这里开始）
```

可选的头：`副触发词`、`副词逻辑`、`延迟几轮`、`包容组`、`不被别的条目点着`（是＝只认聊天原话，别的条目提到它的词不会把它带出来）、`不去点别的条目`。头也可以写英文：`keys`、`keys2`、`logic`、`sticky`、`cooldown`、`delay`、`order`、`constant`、`enabled`、`group`、`exclude_recursion`、`prevent_recursion`。

子文件夹只是你自己分类用的，引擎不看。文件名以 `_` 或 `.` 开头的不算条目，可以放笔记。

## 命令行

```
python -m florilegium check   条目文件夹                  # 重名、两条共用一个触发词、空正文、永远点不着的
python -m florilegium import  lorebook.db 条目文件夹 --apply
python -m florilegium import  lorebook.db 世界书.json --user 名字 --char 名字 --apply
python -m florilegium preview lorebook.db "最新一句" "上一句"   # 只看会点着谁，不动任何状态
python -m florilegium export  lorebook.db 条目文件夹           # 库 -> 文件夹
python -m florilegium tojson  条目文件夹 out.json             # 文件夹 -> SillyTavern 格式
python -m florilegium list    lorebook.db
```

`import` 不加 `--apply` 只看不写。按名字覆盖，条目的 id 不变，粘着和冷却的状态不会因为重新导入丢掉。SillyTavern json 里的 `{{user}}`、`{{char}}`，给了名字才替换，不给就原样留着。

## 我们家的写法

见 [WRITING.md](WRITING.md)。

## 许可

MIT，见 [LICENSE](LICENSE)。

## 测试

```
python -m pytest tests
```

---

### English

Why: someone at our house wanted every scenario at once. But a persona is read every turn, so stuffing every scenario into it makes each turn longer, costlier and
blurrier. Keep the persona short and stable; keep scenarios as keyed entries that come in only when the chat
touches them. Where: order the prompt from most stable to least. Persona and rules first (cache breakpoint),
append-only history next (breakpoint at its end), then everything that changes per turn, including this block,
then the latest user message. Never put the block at the top of the system prompt or inside the history, and
never put a cache breakpoint on it. If your API only takes one system string, merge the block into the latest
user message and leave the system prompt untouched.

`Lorebook(path)` keeps entries, timing state and a log of what fired in one SQLite file. Each turn, call
`build_for_turn(collect_scan_messages(messages), turn_id=...)` and put the returned block after the history,
right before the latest user message (not at the top of the system prompt, or prompt caching breaks every turn).
Entries live as one `.txt` per entry in a folder (head lines, a blank line, then the body; head keys may be
English). `python -m florilegium` imports SillyTavern JSON or a folder, previews what would fire, exports back.
Matching: CJK keys match as substrings, ASCII keys as whole words, `/regex/flags` as regex. Supports secondary
keys with logic, sticky, cooldown, delay, inclusion groups, recursion with per-entry switches, pinning,
constants and a character budget.
