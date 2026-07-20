# SkillGate: result JSON一括検証（＋--fixで末尾ゴミ除去）
# 経緯：result_router_s1s2_s20.json の末尾に約400バイトの余剰空白（json.load()がExtra dataで拒否）。
# 推定原因：同名ファイルへの短い上書き＋OneDrive同期の残尾。書き出し自体はjson.dumps→write_textで正常。
# 恒久策：全result_*.jsonをjson.load()で機械検証する本スクリプトを評価バッチの最後に必ず回す。
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check(path: Path, fix: bool) -> str:
    raw = path.read_text(encoding="utf-8")
    try:
        json.loads(raw)
        return "OK"
    except json.JSONDecodeError:
        obj, end = json.JSONDecoder().raw_decode(raw)  # 先頭から有効なJSONを切り出す
        tail = raw[end:]
        # 空白とNUL(\x00)のみの尾は同期層の残骸として修復可（2026-07-05実例：OneDriveのゼロ埋め442B）
        if tail.strip().strip("\x00"):
            return f"NG: 末尾に実データの余剰 {len(tail)}B（手動確認要）"
        if fix:
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"FIXED: 末尾空白{len(tail)}Bを除去して再書き出し"
        return f"NG: 末尾空白{len(tail)}B（--fixで修復可）"


def main() -> int:
    fix = "--fix" in sys.argv
    bad = 0
    for d in ("bench", "router"):
        for f in sorted((ROOT / d).glob("result_*.json")):
            r = check(f, fix)
            if r != "OK":
                bad += 1
            print(f"[{r}] {d}/{f.name}")
    print(f"\n検証完了。問題 {bad}件" + ("（--fix適用済み）" if fix and bad else ""))
    return 0 if bad == 0 or fix else 1


if __name__ == "__main__":
    sys.exit(main())
