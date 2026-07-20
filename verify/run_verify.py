# SkillGate Phase 3: 出口検証ハーネス（設計図§4）
# スキル本文を強制注入（ルーター通過後の状態を再現）→タスク実行→宣言ルールに機械照合
# →違反があれば1回だけ差し戻し再生成（M-008バックストップ・無限ループ禁止）→再照合。
#
# 成果指標は「層3逸脱の機械検出」。矯正率は参考値（ここを盛らない・設計図§4）。
# ルールはスキル明文要求の部分集合なので、測れるのは逸脱率の下界。
#
#   python3 verify/run_verify.py --model qwen3:8b
#   python3 verify/run_verify.py --mock          # ハーネス自己検証
import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verify"))
from checker import check, load_rules  # noqa: E402

OLLAMA_URL = "http://localhost:11434/api/chat"

SYSTEM_TMPL = """あなたはアシスタント。以下のスキル文書に必ず従って作業すること。

<skill id="{sid}">
{body}
</skill>
"""

SASHIMODOSHI_TMPL = """出力がスキルの次の規則に違反しています。規則に従って全体を出し直してください。
{violations}"""


def skill_body(sid: str) -> str:
    index = json.loads((ROOT / "corpus/index.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in index["skills"]}
    return (ROOT / by_id[sid]["path"]).read_text(encoding="utf-8")


def call_ollama(model, messages, num_ctx=16384, timeout=600):
    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"temperature": 0, "seed": 42, "num_ctx": num_ctx}}
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    content = resp.get("message", {}).get("content") or ""
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


def safe_call(model, messages):
    """1回リトライ。全滅ならNone（呼び出し失敗として率から除外。run_benchと同方式）。"""
    for _ in (1, 2):
        try:
            return call_ollama(model, messages)
        except Exception as e:
            err = f"{type(e).__name__}"
            print(f"  !! 呼び出し失敗: {err}", flush=True)
    return None


MOCK_GOOD = {
    "keihi-seisan": "| 項目 | 判定 | 理由 |\n|---|---|---|\n| 全件 | OK | 適合 |\n上限超過分は要承認。総合判定: 要承認",
    "shiwake-henkan": "日付,借方科目,借方金額,貸方科目,貸方金額,摘要\n4/2,交際費,15000,現金,15000,会食／取引先\n4/8,交通費,880,未払金（従業員立替）,880,客先訪問／立替\n※未承認データのため変換できません。先にkeihi-seisanでチェックしてください。要確認: 研修懇親費",
    "recipe-kanzan": "| 材料 | 換算後 |\n|---|---|\n| 卵 | 1個（0.5個単位に丸め） |\n塩は9割目安。加熱時間は元の8〜10割で様子見。",
    "gijiroku-seikei": "決定事項: 1件\nアクションアイテム\n| 担当 | 内容 | 期日 | 依存 |\n| 佐藤 | チラシ | 期日未定 | なし |\n議論の要約: タナカ（？）氏の件\n保留・持ち越し: 空調\n次回: 未定\n決定事項1件、アクションアイテム1件。",
    "eitango-test": "\n".join(f"{i}. word{i}" for i in range(1, 21)) + "\n# 解答\n" + "\n".join(f"{i}. 答{i}" for i in range(1, 21)),
    "travel-shiori": "日程表: 9:00東京発（所要は要確認）\n宿泊情報: 未定\n持ち物リスト: 標準セット\n緊急連絡先: 記入欄",
}


def call_mock(req: dict, attempt: int) -> str:
    # D01のみ初回逸脱→差し戻し後も逸脱（残存経路の検証）。D04は初回逸脱→差し戻しで矯正。
    if req["id"] == "D01":
        return "全部確認しました。問題ありません。借方に計上してください。"
    if req["id"] == "D04" and attempt == 1:
        return "変換しました。立替分も現金でいいですよね。"
    return MOCK_GOOD[req["skill"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--requests", default="requests/requests_D_draft.json")
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()

    reqfile = json.loads((ROOT / args.requests).read_text(encoding="utf-8"))
    reqs = reqfile["requests"]
    rules_db = load_rules()
    records = []
    for i, req in enumerate(reqs, 1):
        system = SYSTEM_TMPL.format(sid=req["skill"], body=skill_body(req["skill"]))
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": req["prompt"]}]
        out1 = call_mock(req, 1) if args.mock else safe_call(args.model, msgs)
        if out1 is None:  # 呼び出し失敗＝測定不能。逸脱扱いにしない
            records.append({"id": req["id"], "skill": req["skill"], "error": "呼び出し失敗",
                            "deviated": None, "corrected": None,
                            "violations_first": [], "violations_after_retry": None})
            print(f"[{i}/{len(reqs)}] {req['id']} 測定不能（除外）", flush=True)
            continue
        v1 = check(out1, req["skill"], req.get("flags"), rules_db)
        out2, v2 = None, None
        if v1:  # 有界差し戻し：1回だけ（M-008）
            fb = SASHIMODOSHI_TMPL.format(violations="\n".join(f"- {x['desc']}" for x in v1))
            msgs2 = msgs + [{"role": "assistant", "content": out1},
                            {"role": "user", "content": fb}]
            out2 = call_mock(req, 2) if args.mock else safe_call(args.model, msgs2)
            if out2 is None:
                out2, v2 = "", v1  # 差し戻し先が失敗→初回違反のまま残存扱い（保守的）
            else:
                v2 = check(out2, req["skill"], req.get("flags"), rules_db)
        records.append({"id": req["id"], "skill": req["skill"], "flags": req.get("flags"),
                        "error": None,
                        "violations_first": v1, "violations_after_retry": v2,
                        "deviated": bool(v1),
                        "corrected": (bool(v1) and v2 == []),
                        "out_first_head": out1[:300],
                        "out_retry_head": (out2 or "")[:300],
                        "out_first_full": out1,   # 全文保存：ルール較正時のオフライン再照合用
                        "out_retry_full": out2})
        print(f"[{i}/{len(reqs)}] {req['id']} 逸脱={bool(v1)}"
              + (f" 差し戻し後残存={bool(v2)}" if v1 else ""), flush=True)

    ok_recs = [r for r in records if r.get("error") is None]
    n = len(ok_recs)
    dev = [r for r in ok_recs if r["deviated"]]
    results = {
        "n": n,
        "測定不能_除外": len(records) - n,
        "逸脱率_初回": round(len(dev) / n, 4) if n else None,
        "違反ルール延べ数_初回": sum(len(r["violations_first"]) for r in ok_recs),
        "差し戻し後残存率_逸脱分母": round(sum(1 for r in dev if r["violations_after_retry"]) / len(dev), 4) if dev else None,
        "矯正率_参考値": round(sum(1 for r in dev if r["corrected"]) / len(dev), 4) if dev else None,
    }
    result = {
        "test": "SkillGate Phase3 exit verify",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "design": {"model": ("MOCK" if args.mock else args.model),
                   "requests_file": args.requests, "temperature": 0, "num_ctx": 16384,
                   "注入": "スキル本文全文を強制注入（層1・層2を排除して層3を単離）",
                   "差し戻し": "1回まで（M-008バックストップ）",
                   "判定": "宣言ルール（rules.json）の機械照合。LLM judge不使用"},
        "results": results,
        "caveats": ["1試行", "ルールは明文要求の部分集合＝逸脱率は下界",
                    "矯正率は参考値（成果指標は検出）",
                    "requests status: " + reqfile.get("status", "不明")],
        "artifacts": {"records": records},
    }
    tag = "mock" if args.mock else args.model.replace(":", "_")
    out = ROOT / "verify" / f"result_verify_{tag}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== 集計 ==")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"→ {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
