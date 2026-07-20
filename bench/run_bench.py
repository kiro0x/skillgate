# SkillGate Phase 1 ベンチランナー
# 規約準拠：素Python＋ファイルI/O＋Ollama直接HTTP。外部ライブラリ・ブラウザ経由なし。
# 判定：Ollamaネイティブtool_calls（機械照合）。LLM judge不使用。
#
# 使い方:
#   python3 run_bench.py --model qwen3:8b --skills 20 --turn 1
#   python3 run_bench.py --mock --skills 5 --turn 30      # ハーネス自己検証用
#
# 指標（設計図§2-3）:
#   サボり率 = A群で read_skill 未呼び出しの割合
#   誤起動率 = B群で read_skill を呼んだ割合
#   航法精度 = C群で正スキル＋正セクションを指定した割合
#   （遵守率=層3はPhase 3のfive_verify拡張で測る。本ランナーの対象外）
import argparse
import json
import random
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # SkillGate/
OLLAMA_URL = "http://localhost:11434/api/chat"

TOOL_DEF = [{
    "type": "function",
    "function": {
        "name": "read_skill",
        "description": "作業を始める前に、該当するスキルの本文（またはセクション）を読み込む。該当スキルがあるなら必ず先に呼ぶこと。",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "スキルID（一覧のidと一致させる）"},
                "section": {"type": "string", "description": "読むセクションの見出し（任意。特定手順だけ必要なとき）"},
            },
            "required": ["skill_id"],
        },
    },
}]

SYSTEM_TMPL = """あなたはアシスタント。以下のスキル一覧を持つ。
依頼がいずれかのスキルの担当領域なら、回答の前に必ず read_skill ツールでそのスキルを読むこと。
特定の手順・規則だけが必要なら section も指定する。どのスキルにも該当しない依頼は、ツールを呼ばず普通に答える。

# スキル一覧
{skill_list}
"""

# 固定台本の埋めターン（決定論。M系列の固定台本文化に合わせ、モデル呼び出しなし）
FILLER_USER = [
    "今日は蒸し暑いね。", "さっきコーヒーをこぼしそうになった。", "週末は特に予定がないんだ。",
    "最近ちょっと寝不足で。", "駅前に新しいパン屋ができたらしい。", "腕時計の電池が切れかけてる。",
    "観葉植物の葉が1枚黄色くなってた。", "隣の部屋の時計が5分進んでる。", "自転車のタイヤの空気が甘い気がする。",
    "夕方から雨らしいよ。",
]
FILLER_ASSIST = [
    "そうなんですね。", "なるほど。", "それは何よりです。", "お気をつけて。", "いいですね。",
]


def build_skill_list(index: dict, request: dict, n_skills: int, seed: int) -> list:
    """expectedを必ず含め、シードで決定論的に残りを埋めてシャッフルした部分集合を返す。"""
    all_ids = [s["id"] for s in index["skills"]]
    need = [i for i in request.get("expected", []) if i in all_ids]
    rng = random.Random(f"{seed}:{request['id']}")
    pool = [i for i in all_ids if i not in need]
    rng.shuffle(pool)
    chosen = (need + pool)[:max(n_skills, len(need))]
    rng.shuffle(chosen)
    by_id = {s["id"]: s for s in index["skills"]}
    return [by_id[i] for i in chosen]


def render_skill_list(skills: list) -> str:
    out = []
    for s in skills:
        secs = "／".join(x["title"] for x in s["sections"] if x["level"] <= 2)
        out.append(f"- id: {s['id']}\n  説明: {s['description']}\n  セクション: {secs}")
    return "\n".join(out)


def build_messages(system: str, prompt: str, turn: int) -> list:
    msgs = [{"role": "system", "content": system}]
    for i in range(turn - 1):
        msgs.append({"role": "user", "content": FILLER_USER[i % len(FILLER_USER)]})
        msgs.append({"role": "assistant", "content": FILLER_ASSIST[i % len(FILLER_ASSIST)]})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def call_ollama(model: str, messages: list, timeout: int = 600, num_ctx: int = 16384) -> dict:
    # num_ctx明示は必須。Ollama既定4096だと黙って切り捨てられ、条件が壊れる（2026-07-05の教訓）
    payload = {"model": model, "messages": messages, "tools": TOOL_DEF,
               "stream": False,
               "options": {"temperature": 0, "seed": 42, "num_ctx": num_ctx}}
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def call_mock(request: dict, skills: list) -> dict:
    """ハーネス自己検証用の決定論モック。GT通りに振る舞う理想モデル＋既知の失敗2種を混ぜる。"""
    rid = request["id"]
    exp = request.get("expected", [])
    # 意図的な失敗注入：A05=サボり、B19=誤起動、C13=誤セクション（照合ロジックの検出確認用）
    if rid == "A05":
        return {"message": {"content": "はい、作ります。", "tool_calls": []}}
    if rid == "B19":
        return {"message": {"content": "", "tool_calls": [
            {"function": {"name": "read_skill", "arguments": {"skill_id": "data-bunseki"}}}]}}
    if rid == "C13":
        return {"message": {"content": "", "tool_calls": [
            {"function": {"name": "read_skill",
                          "arguments": {"skill_id": "shougai-runbook", "section": "手順2：応急処置"}}}]}}
    if not exp:
        return {"message": {"content": "普通に答えます。", "tool_calls": []}}
    calls = []
    for e in exp:
        args = {"skill_id": e}
        if request.get("expected_section"):
            args["section"] = request["expected_section"]
        calls.append({"function": {"name": "read_skill", "arguments": args}})
    return {"message": {"content": "", "tool_calls": calls}}


def extract_calls(resp: dict) -> list:
    calls = []
    for tc in (resp.get("message", {}).get("tool_calls") or []):
        fn = tc.get("function", {})
        if fn.get("name") != "read_skill":
            continue
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append({"skill_id": args.get("skill_id"), "section": args.get("section")})
    return calls


def norm(s):
    return (s or "").strip().lower().replace("：", ":").replace(" ", "")


def judge(request: dict, calls: list) -> dict:
    """機械照合。group別に正誤を確定する。"""
    g = request["group"]
    called_ids = {c["skill_id"] for c in calls if c["skill_id"]}
    ok_ids = set(request.get("expected", [])) | set(request.get("accept_also", []))
    if g == "B":
        return {"fired": bool(calls), "correct": not calls}
    if g == "A":
        hit = bool(called_ids & ok_ids)
        return {"fired": bool(calls), "correct": hit, "called": sorted(called_ids)}
    if g == "C":
        want_sec = norm(request.get("expected_section"))
        sec_hit = any(c["skill_id"] in ok_ids and norm(c["section"]) == want_sec for c in calls)
        skill_hit = bool(called_ids & ok_ids)
        return {"fired": bool(calls), "skill_correct": skill_hit,
                "correct": sec_hit, "called": calls}
    raise ValueError(f"unknown group {g}")


def aggregate(records: list) -> dict:
    def rate(nume, deno):
        return round(nume / deno, 4) if deno else None
    n_err = sum(1 for r in records if r.get("error"))
    ok = [r for r in records if not r.get("error")]  # 呼び出し失敗は率から除外（サボり扱いにしない）
    A = [r for r in ok if r["group"] == "A"]
    B = [r for r in ok if r["group"] == "B"]
    C = [r for r in ok if r["group"] == "C"]
    return {
        "n": {"A": len(A), "B": len(B), "C": len(C), "呼び出し失敗_除外": n_err},
        "サボり率_A未起動": rate(sum(1 for r in A if not r["judge"]["fired"]), len(A)),
        "A正起動率": rate(sum(1 for r in A if r["judge"]["correct"]), len(A)),
        "誤起動率_B起動": rate(sum(1 for r in B if r["judge"]["fired"]), len(B)),
        "C_スキル正解率": rate(sum(1 for r in C if r["judge"].get("skill_correct")), len(C)),
        "航法精度_Cセクション正解": rate(sum(1 for r in C if r["judge"]["correct"]), len(C)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--skills", type=int, default=20, help="一覧に載せるスキル数（5/20/40）")
    ap.add_argument("--turn", type=int, default=1, help="依頼を出すターン（1/30/120）")
    ap.add_argument("--requests", default="requests/requests_draft.json")
    ap.add_argument("--groups", default="A,B,C")
    ap.add_argument("--limit", type=int, default=0, help="先頭N問だけ（0=全部）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-ctx", type=int, default=16384,
                    help="コンテキスト長。既定4096のままだと切り捨てで測定が壊れる")
    ap.add_argument("--mock", action="store_true", help="Ollama不要の自己検証モード")
    args = ap.parse_args()

    index = json.loads((ROOT / "corpus/index.json").read_text(encoding="utf-8"))
    reqfile = json.loads((ROOT / args.requests).read_text(encoding="utf-8"))
    groups = set(args.groups.split(","))
    reqs = [r for r in reqfile["requests"] if r["group"] in groups]
    if args.limit:
        reqs = reqs[:args.limit]

    n_avail = len(index["skills"])
    caveats = ["1試行（n=1）。断定不可", "固定台本の埋めターン（実会話ではない）",
               "ground truth: " + reqfile.get("status", "不明")]
    if args.skills > n_avail:
        caveats.append(f"要求スキル数{args.skills} > コーパス{n_avail}本 → {n_avail}で実行")
    if args.mock:
        caveats.append("mockモード：ハーネス検証用。モデル測定値ではない")

    records = []
    for i, req in enumerate(reqs, 1):
        subset = build_skill_list(index, req, min(args.skills, n_avail), args.seed)
        system = SYSTEM_TMPL.format(skill_list=render_skill_list(subset))
        messages = build_messages(system, req["prompt"], args.turn)
        error = None
        if args.mock:
            resp = call_mock(req, subset)
        else:
            resp = None
            for attempt in (1, 2):  # タイムアウト等は1回だけリトライ。条件ごと落とさない
                try:
                    resp = call_ollama(args.model, messages, num_ctx=args.num_ctx)
                    error = None  # リトライ成功なら失敗扱いにしない
                    break
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"
                    print(f"  !! {req['id']} 呼び出し失敗(試行{attempt}): {error}", flush=True)
            if resp is None:
                resp = {"message": {"content": "", "tool_calls": []}}
        calls = extract_calls(resp)
        rec = {"id": req["id"], "group": req["group"], "error": error,
               "subtype": req.get("subtype"),
               "prompt": req["prompt"], "expected": req.get("expected"),
               "expected_section": req.get("expected_section"),
               "n_skills_listed": len(subset), "calls": calls,
               "prompt_eval_count": resp.get("prompt_eval_count"),  # 実消費トークン（切り捨て検知用）
               "judge": judge(req, calls),
               "raw_content_head": (resp.get("message", {}).get("content") or "")[:200]}
        records.append(rec)
        print(f"[{i}/{len(reqs)}] {req['id']} fired={rec['judge']['fired']} "
              f"correct={rec['judge']['correct']}", flush=True)

    result = {
        "test": "SkillGate Phase1 bench",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "design": {"model": ("MOCK" if args.mock else args.model),
                   "n_skills": min(args.skills, n_avail), "turn": args.turn,
                   "seed": args.seed, "requests_file": args.requests,
                   "temperature": 0, "num_ctx": args.num_ctx,
                   "judge": "機械照合（tool_callsログ）"},
        "results": aggregate(records),
        "caveats": caveats,
        "artifacts": {"records": records},
    }
    tag = "mock" if args.mock else args.model.replace(":", "_")
    stem = Path(args.requests).stem.replace("requests_", "")
    suffix = "" if stem == "v1" else f"_{stem}"  # v1以外は別名（本ベンチ結果の上書き防止）
    out = ROOT / "bench" / f"result_{tag}_s{min(args.skills, n_avail)}_t{args.turn}{suffix}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== 集計 ==")
    print(json.dumps(result["results"], ensure_ascii=False, indent=2))
    print(f"→ {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
