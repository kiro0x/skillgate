# SkillGate Phase 2: 決定論ルーター（入口駅の住人・⑤の統治の第2実装）
# 構成（設計図§3）: stage1 語彙一致（決定論）→ stage2 小型LLM分類（低temp）→ 和集合＋セクション選択
# 方針: 見逃し優先で潰す（注入しすぎ側に倒す）。誤爆はstage2の棄却と注入の無害化で抑える（M-005二段構え）。
# 規約準拠: 素Python＋Ollama直HTTP。判定に関わる状態は持たない（毎リクエスト独立・決定論）。
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # SkillGate/
OLLAMA_URL = "http://localhost:11434/api/chat"

STAGE2_SYSTEM = """あなたはスキル分類器。ユーザーの依頼文を読み、下のスキル一覧から「本文を読むべきスキル」を選ぶ。
規則:
- 依頼がスキルの担当領域の作業依頼であるときだけ選ぶ。単なる知識質問・雑談・語句の意味の質問は該当なし。
- 表面的に同じ単語があっても、作業内容が違えば選ばない。
- 複数該当は複数選ぶ。該当なしは空配列。
- 依頼がスキル内の特定の規則・手順だけを必要とする場合、そのスキルのセクション一覧から見出しを1つ選んで sections に入れる。
- 出力はJSONのみ: {"skills": ["id", ...], "sections": {"id": "見出し"}}

# スキル一覧
{skill_list}
"""

# v2.1追加規則（出所：B2追試の誤射 B32/B58/B59。採点はA2B3で行う＝濁り分離）
STAGE2_RULE_V21 = """- 次は作業依頼ではないので選ばない: 用語の意味・由来・一般知識・相場を尋ねる質問／語呂合わせや名前案などの創作の依頼／スキルの担当対象ではない一般的な物や家電の操作方法の質問。
"""


def load_router(root: Path = ROOT):
    index = json.loads((root / "corpus/index.json").read_text(encoding="utf-8"))
    trig = json.loads((root / "router/triggers.json").read_text(encoding="utf-8"))["triggers"]
    return index, trig


def norm(s: str) -> str:
    return s.lower()


def stage1(text: str, triggers: dict, skill_ids: set) -> list:
    """決定論の語彙一致。対象スキル集合内のみ返す。"""
    t = norm(text)
    hits = []
    for sid, words in triggers.items():
        if sid in skill_ids and any(norm(w) in t for w in words):
            hits.append(sid)
    return hits


def render_skill_list(skills: list) -> str:
    out = []
    for s in skills:
        secs = "／".join(x["title"] for x in s["sections"] if x["level"] <= 2)
        out.append(f"- id: {s['id']}\n  説明: {s['description']}\n  セクション: {secs}")
    return "\n".join(out)


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def stage2(text: str, skills: list, model: str = "qwen3:8b",
           num_ctx: int = 16384, timeout: int = 600, prompt_version: str = "v2") -> dict:
    system = STAGE2_SYSTEM.replace("{skill_list}", render_skill_list(skills))
    if prompt_version == "v2.1":
        system = system.replace("- 出力はJSONのみ", STAGE2_RULE_V21 + "- 出力はJSONのみ")
    payload = {"model": model, "format": "json", "stream": False,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": text}],
               "options": {"temperature": 0, "seed": 42, "num_ctx": num_ctx}}
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    content = strip_think(resp.get("message", {}).get("content") or "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"skills": [], "sections": {}, "parse_error": True}
    valid = {s["id"] for s in skills}
    sk = [x for x in (parsed.get("skills") or []) if x in valid]
    secs_in = parsed.get("sections") or {}
    secs = {}
    by_id = {s["id"]: s for s in skills}
    for sid, sec in (secs_in.items() if isinstance(secs_in, dict) else []):
        if sid in valid and isinstance(sec, str):
            titles = [x["title"] for x in by_id[sid]["sections"]]
            if sec in titles:
                secs[sid] = sec
    return {"skills": sk, "sections": secs, "parse_error": False}


def route(text: str, skills: list, triggers: dict, model: str = "qwen3:8b",
          use_stage2: bool = True, prompt_version: str = "v2") -> dict:
    """skills: 対象スキル（index.jsonのエントリのリスト）。返り値に判断の全記録を残す（監査ログ）。"""
    ids = {s["id"] for s in skills}
    s1 = stage1(text, triggers, ids)
    s2 = {"skills": [], "sections": {}, "parse_error": None}
    if use_stage2:
        try:
            s2 = stage2(text, skills, model=model, prompt_version=prompt_version)
        except Exception:
            s2 = {"skills": [], "sections": {}, "parse_error": True}
    # ポリシーv2（2026-07-05）: stage2主・stage1はstage2故障時のみのフォールバック。
    # v1（和集合・stage1床）は誤起動33%で棄却——stage2の棄却をstage1の保険が上書きしていた。
    # 注意: この選択は同一90問のIF分析による（チューニング濁り）。新規B群での追試が確定条件。
    if use_stage2 and not s2["parse_error"]:
        final = sorted(set(s2["skills"]))
    else:
        final = sorted(set(s1))  # 決定論フォールバック（stage2故障時も丸腰にしない）
    return {"skills": final, "sections": s2["sections"],
            "stage1_hits": s1, "stage2_hits": s2["skills"],
            "stage2_parse_error": s2["parse_error"]}


def build_injection(decision: dict, root: Path = ROOT) -> str:
    """該当スキル本文（セクション指定があればその節のみ）をプロンプト注入用テキストに組む。
    無害化文言込み（誤爆しても本体の仕事を壊さない）。"""
    if not decision["skills"]:
        return ""
    index, _ = load_router(root)
    by_id = {s["id"]: s for s in index["skills"]}
    parts = []
    for sid in decision["skills"]:
        s = by_id.get(sid)
        if not s:
            continue
        body = (root / s["path"]).read_text(encoding="utf-8")
        sec = decision["sections"].get(sid)
        if sec:
            m = re.search(rf"^#+\s*{re.escape(sec)}\s*$(.*?)(?=^#+\s|\Z)",
                          body, re.MULTILINE | re.DOTALL)
            if m:
                body = f"## {sec}\n{m.group(1).strip()}"
        parts.append(f"<skill id=\"{sid}\">\n{body}\n</skill>")
    return ("以下は今回の依頼に関係する可能性があるスキル文書。関係する場合は必ずこの手順に従うこと。"
            "関係しない場合は無視してよい。\n" + "\n".join(parts))
