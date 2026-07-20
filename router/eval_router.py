# SkillGate Phase 2: ルーター評価（Phase 1と同一ベンチ・同一判定基準）
# 合格基準（設計図§3）: サボり率(見逃し)が素の1/10以下、誤起動率が素の2倍以内
# ルーターは会話履歴を見ない設計なので、ターン条件は構造的に無関係（＝希釈耐性が設計から出る）。
# スキル数条件（--skills）はPhase 1と同じ部分集合生成ロジックで揃える。
#
#   python3 router/eval_router.py --skills 20                # stage1+stage2
#   python3 router/eval_router.py --skills 20 --no-stage2    # stage1のみ（決定論・Ollama不要）
import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "router"))
from route import load_router, route  # noqa: E402


def build_subset(index, request, n_skills, seed):
    # run_bench.pyと同一ロジック（expected必含・シード決定論）
    all_ids = [s["id"] for s in index["skills"]]
    need = [i for i in request.get("expected", []) if i in all_ids]
    rng = random.Random(f"{seed}:{request['id']}")
    pool = [i for i in all_ids if i not in need]
    rng.shuffle(pool)
    chosen = (need + pool)[:max(n_skills, len(need))]
    rng.shuffle(chosen)
    by_id = {s["id"]: s for s in index["skills"]}
    return [by_id[i] for i in chosen]


def norm(s):
    return (s or "").strip().lower().replace("：", ":").replace(" ", "")


def judge(req, decision):
    g = req["group"]
    got = set(decision["skills"])
    ok_ids = set(req.get("expected", [])) | set(req.get("accept_also", []))
    if g == "B":
        return {"fired": bool(got), "correct": not got}
    if g == "A":
        return {"fired": bool(got), "correct": bool(got & ok_ids)}
    if g == "C":
        want = norm(req.get("expected_section"))
        sec_hit = any(sid in ok_ids and norm(sec) == want
                      for sid, sec in decision["sections"].items())
        return {"fired": bool(got), "skill_correct": bool(got & ok_ids), "correct": sec_hit}
    raise ValueError(g)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills", type=int, default=20)
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--requests", default="requests/requests_v1.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-stage2", action="store_true")
    ap.add_argument("--prompt-version", default="v2", choices=["v2", "v2.1"])
    args = ap.parse_args()

    index, triggers = load_router(ROOT)
    reqs = json.loads((ROOT / args.requests).read_text(encoding="utf-8"))["requests"]
    use2 = not args.no_stage2

    records = []
    for i, req in enumerate(reqs, 1):
        subset = build_subset(index, req, args.skills, args.seed)
        try:
            d = route(req["prompt"], subset, triggers, model=args.model,
                      use_stage2=use2, prompt_version=args.prompt_version)
            err = None
        except Exception as e:
            d = {"skills": [], "sections": {}, "stage1_hits": [], "stage2_hits": [],
                 "stage2_parse_error": None}
            err = f"{type(e).__name__}: {e}"
        j = judge(req, d)
        records.append({"id": req["id"], "group": req["group"], "error": err,
                        "decision": d, "judge": j,
                        "expected": req.get("expected"),
                        "expected_section": req.get("expected_section")})
        print(f"[{i}/{len(reqs)}] {req['id']} -> {d['skills']} "
              f"correct={j['correct']}", flush=True)

    def rate(n, d):
        return round(n / d, 4) if d else None
    ok = [r for r in records if not r["error"]]
    A = [r for r in ok if r["group"] == "A"]
    B = [r for r in ok if r["group"] == "B"]
    C = [r for r in ok if r["group"] == "C"]
    results = {
        "n": {"A": len(A), "B": len(B), "C": len(C),
              "エラー除外": len(records) - len(ok)},
        "見逃し率_A未注入": rate(sum(1 for r in A if not r["judge"]["fired"]), len(A)),
        "A正注入率": rate(sum(1 for r in A if r["judge"]["correct"]), len(A)),
        "誤起動率_B注入": rate(sum(1 for r in B if r["judge"]["fired"]), len(B)),
        "C_スキル正解率": rate(sum(1 for r in C if r["judge"].get("skill_correct")), len(C)),
        "航法精度_Cセクション正解": rate(sum(1 for r in C if r["judge"]["correct"]), len(C)),
        "stage2パースエラー": sum(1 for r in ok if r["decision"]["stage2_parse_error"]),
    }
    mode = "s1only" if args.no_stage2 else "s1s2"
    result = {
        "test": "SkillGate Phase2 router eval",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "design": {"mode": mode, "stage2_model": (None if args.no_stage2 else args.model),
                   "prompt_version": args.prompt_version,
                   "n_skills": args.skills, "seed": args.seed,
                   "triggers": "SKILL.md説明文のみ由来（リーク規律）",
                   "判定": "機械照合（ルーター出力 vs ground truth）"},
        "baseline_比較用": "bench/マトリクス.md の同スキル数条件と比較する",
        "results": results,
        "caveats": ["1試行", "ルーターは会話履歴非参照＝ターン条件は構造的に不変",
                    "作問者と実装者が同一AIである点は残留リスク（トリガーは説明文由来に限定して緩和）"],
        "artifacts": {"records": records},
    }
    stem = Path(args.requests).stem.replace("requests_", "")
    pv = args.prompt_version.replace(".", "")
    out = ROOT / "router" / f"result_router_{mode}_{pv}_s{args.skills}_{stem}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== 集計 ==")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"→ {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
