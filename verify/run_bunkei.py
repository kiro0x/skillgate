# SkillGate 副菜: ルール文型×遵守率実験（層3の続き）
# 同一ルールを5条件（現行/禁止形/手順形/例示付き/理由付き）でスキル本文に埋め、
# 同一タスク（D群流用）で初回遵守率を測る。差し戻しなし（初回遵守が測定対象）。
# 判定は当該rule_idのみ照合（他ルールの違反はこの実験の対象外）。
#   python3 verify/run_bunkei.py --model qwen3:8b
#   python3 verify/run_bunkei.py --mock
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
    for _ in (1, 2):
        try:
            return call_ollama(model, messages)
        except Exception as e:
            print(f"  !! 呼び出し失敗: {type(e).__name__}", flush=True)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()

    spec = json.loads((ROOT / "verify/bunkei_variants.json").read_text(encoding="utf-8"))
    reqs = {r["id"]: r for r in json.loads(
        (ROOT / "requests/requests_D_v1.json").read_text(encoding="utf-8"))["requests"]}
    index = json.loads((ROOT / "corpus/index.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in index["skills"]}
    rules_db = load_rules()

    records = []
    jobs = []
    for rule in spec["rules"]:
        forms = {"現行": None, **rule["forms"]}
        for form, variant in forms.items():
            for tid in rule["task_ids"]:
                jobs.append((rule, form, variant, tid))
    print(f"総実行数: {len(jobs)}", flush=True)

    for i, (rule, form, variant, tid) in enumerate(jobs, 1):
        req = reqs[tid]
        body = (ROOT / by_id[rule["skill"]]["path"]).read_text(encoding="utf-8")
        if variant is not None:
            if rule["target"] not in body:
                records.append({"rule_id": rule["rule_id"], "form": form, "task": tid,
                                "error": "target不一致", "comply": None})
                print(f"[{i}] !! target不一致 {rule['rule_id']}", flush=True)
                continue
            body = body.replace(rule["target"], variant)
        msgs = [{"role": "system", "content": SYSTEM_TMPL.format(sid=rule["skill"], body=body)},
                {"role": "user", "content": req["prompt"]}]
        if args.mock:
            # カナリア: K2×A_禁止形のみ違反出力、他は目的ルールに適合する固定出力
            out = ("総合判定: 合格" if (rule["rule_id"] == "K2" and form == "A_禁止形")
                   else MOCK_OK[rule["rule_id"]])
        else:
            out = safe_call(args.model, msgs)
        if out is None:
            records.append({"rule_id": rule["rule_id"], "form": form, "task": tid,
                            "error": "呼び出し失敗", "comply": None})
            continue
        vios = check(out, rule["skill"], req.get("flags"), rules_db)
        hit = any(v["rule_id"] == rule["rule_id"] for v in vios)
        records.append({"rule_id": rule["rule_id"], "form": form, "task": tid,
                        "error": None, "comply": (not hit), "out_full": out})
        print(f"[{i}/{len(jobs)}] {rule['rule_id']}×{form}×{tid} 遵守={not hit}", flush=True)

    # 集計: form別・rule別
    def agg(keyf):
        table = {}
        for r in records:
            if r["comply"] is None:
                continue
            k = keyf(r)
            table.setdefault(k, [0, 0])
            table[k][1] += 1
            table[k][0] += 1 if r["comply"] else 0
        return {k: {"遵守": v[0], "n": v[1], "率": round(v[0] / v[1], 4)}
                for k, v in sorted(table.items())}

    results = {"form別": agg(lambda r: r["form"]),
               "rule×form": agg(lambda r: f"{r['rule_id']}×{r['form']}"),
               "エラー除外": sum(1 for r in records if r["comply"] is None)}
    result = {
        "test": "SkillGate 文型×遵守率実験",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "design": {"model": ("MOCK" if args.mock else args.model), "temperature": 0,
                   "num_ctx": 16384, "差し戻し": "なし（初回遵守のみ）",
                   "判定": "当該rule_idのみの機械照合",
                   "事前予測": spec["_prediction"]},
        "results": results,
        "caveats": ["1試行", "タスク数はルールにより1〜3で不均等", spec["_caveat"],
                    "書き換えの意味同一性は作成者の目視承認済み（2026-07-05）"],
        "artifacts": {"records": records},
    }
    tag = "mock" if args.mock else args.model.replace(":", "_")
    out_p = ROOT / "verify" / f"result_bunkei_{tag}.json"
    out_p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== form別 ==")
    print(json.dumps(results["form別"], ensure_ascii=False, indent=2))
    print(f"→ {out_p.relative_to(ROOT)}")
    return 0


MOCK_OK = {
    "K2": "| 項目 | 判定 | 理由 |\n|---|---|---|\n| 全件 | OK | 適合 |\n総合判定: 承認可",
    "R2": "| 材料 | 換算後 |\n|---|---|\n| 卵 | 1.5個→0.5個単位で1.5個 |\n※時間は元の8〜10割で様子見。半分・9割・味の注記。",
    "G4": "出席: 山田、タナカ（？）氏。決定事項1件、件数報告。| 担当 | 内容 | 期日 | 依存 |",
    "E1": "\n".join(f"{i}. word{i}" for i in range(1, 21)) + "\n# 解答\n1. 答",
    "G1": "ヘッダー: 6/20\n決定事項: 1件\nアクションアイテム\n| 担当 | 内容 | 期日 | 依存 |\n議論の要約\n保留・持ち越し\n次回予定\n件数: 1件",
    "S3": "未承認データのため変換できません。先にkeihi-seisanでチェックしてください。",
}

if __name__ == "__main__":
    sys.exit(main())
