# SkillGate 条件行列の一括実行＋マトリクス集計
# 作成者のローカル（Ollama稼働環境）で実行する。素Python・依存なし。
#
#   python bench/run_matrix.py                    # qwen3:8b と qwen3:14b の全条件
#   python bench/run_matrix.py --models qwen3:8b  # 1モデルだけ
#   python bench/run_matrix.py --summarize-only   # 既存result_*.jsonから表だけ再生成
#
# 出力：bench/result_*.json（条件ごと）＋ bench/マトリクス.md（1枚表）
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # bench/
SKILLS = [5, 20]   # 40はコーパス20本のため保留（合成20本追加後に解禁）
TURNS = [1, 30, 120]


def run_all(models: list) -> None:
    total = len(models) * len(SKILLS) * len(TURNS)
    i = 0
    for m in models:
        for s in SKILLS:
            for t in TURNS:
                i += 1
                print(f"\n===== [{i}/{total}] {m} skills={s} turn={t} =====", flush=True)
                cmd = [sys.executable, str(HERE / "run_bench.py"),
                       "--model", m, "--skills", str(s), "--turn", str(t),
                       "--requests", "requests/requests_v1.json"]
                r = subprocess.run(cmd)
                if r.returncode != 0:
                    print(f"!! 失敗: {m} s{s} t{t}（続行）", flush=True)


def summarize() -> None:
    rows = []
    for f in sorted(HERE.glob("result_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        des, res = d.get("design", {}), d.get("results", {})
        if des.get("model") == "MOCK":
            continue  # mockはハーネス検証用。表に混ぜない
        if not str(des.get("requests_file", "")).endswith("requests_v1.json"):
            continue  # 追試用リクエスト集合の結果は本マトリクスに混ぜない
        rows.append({
            "model": des.get("model"), "skills": des.get("n_skills"),
            "turn": des.get("turn"),
            "サボり率": res.get("サボり率_A未起動"),
            "A正起動率": res.get("A正起動率"),
            "誤起動率": res.get("誤起動率_B起動"),
            "航法精度": res.get("航法精度_Cセクション正解"),
            "file": f.name,
        })
    rows.sort(key=lambda r: (str(r["model"]), r["skills"] or 0, r["turn"] or 0))
    lines = [
        "# SkillGate Phase 1 サボり率マトリクス",
        "",
        "n=1（1試行）。判定＝tool_callsログの機械照合。ground truth＝requests_v1.json（作成者の目視承認 2026-07-04）。",
        "",
        "| モデル | スキル数 | 依頼ターン | サボり率(A) | A正起動率 | 誤起動率(B) | 航法精度(C) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        def pct(v):
            return f"{v*100:.1f}%" if isinstance(v, (int, float)) else "—"
        lines.append(f"| {r['model']} | {r['skills']} | {r['turn']} | "
                     f"{pct(r['サボり率'])} | {pct(r['A正起動率'])} | "
                     f"{pct(r['誤起動率'])} | {pct(r['航法精度'])} |")
    if not rows:
        lines.append("|（実測結果なし。run_matrix.pyを実行）| | | | | | |")
    lines += ["", "caveats: 1試行・固定台本埋めターン・スキル40本条件は未実施（コーパス20本）。"]
    out = HERE / "マトリクス.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"→ {out}（{len(rows)}行）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen3:8b,qwen3:14b")
    ap.add_argument("--summarize-only", action="store_true")
    args = ap.parse_args()
    if not args.summarize_only:
        run_all([m.strip() for m in args.models.split(",") if m.strip()])
    summarize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
