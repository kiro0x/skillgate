# SkillGate Phase 3: 出口照合エンジン（凍結対象。関心事の追加はrules.jsonの宣言で行う）
# 対応ルール型: must_match / must_not_match / count_min / order
# 戻り値: 違反リスト（空＝適合）。判定は正規表現の機械照合のみ。LLM judge不使用。
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_rules() -> dict:
    return json.loads((HERE / "rules.json").read_text(encoding="utf-8"))["rules"]


def _flags(rule: dict) -> int:
    return re.MULTILINE if "M" in rule.get("flags", "") else 0


def check(output: str, skill_id: str, request_flags: list = None,
          rules_db: dict = None) -> list:
    """出力テキストをskill_idの宣言ルールに照合し、違反を返す。"""
    rules_db = rules_db or load_rules()
    request_flags = request_flags or []
    violations = []
    for rule in rules_db.get(skill_id, []):
        if rule.get("when") and rule["when"] not in request_flags:
            continue  # 条件付きルール：このリクエストには適用外
        if rule.get("unless") and rule["unless"] in request_flags:
            continue  # 除外条件：例）拒否すべき場面に出力形式ルールを適用しない（D05偽陽性の教訓）
        rtype = rule["type"]
        ok = True
        if rtype == "must_match":
            ok = re.search(rule["pattern"], output, _flags(rule)) is not None
        elif rtype == "must_not_match":
            ok = re.search(rule["pattern"], output, _flags(rule)) is None
        elif rtype == "count_min":
            ok = len(re.findall(rule["pattern"], output, _flags(rule))) >= rule["n"]
        elif rtype == "order":
            pos, prev = [], -1
            for p in rule["patterns"]:
                m = re.search(p, output)
                pos.append(m.start() if m else None)
            found = [x for x in pos if x is not None]
            ok = len(found) == len(pos) and found == sorted(found)
        else:
            raise ValueError(f"未知のルール型: {rtype}")
        if not ok:
            violations.append({"rule_id": rule["id"], "desc": rule["desc"]})
    return violations


if __name__ == "__main__":
    # 自己検証（機械照合の検出能力をモックで確認）
    good_keihi = """| 項目 | 判定 | 理由 |
|---|---|---|
| 日付 | OK | 申請月内 |
交際費が上限超過のため要承認。総合判定: 要承認"""
    bad_keihi = "チェックしました。全部OKです。借方:交際費 貸方:現金"
    v1 = check(good_keihi, "keihi-seisan", ["上限超過あり"])
    v2 = check(bad_keihi, "keihi-seisan", ["上限超過あり"])
    print("適合出力の違反:", v1)
    print("逸脱出力の違反:", [x["rule_id"] for x in v2])
    assert v1 == [] and {x["rule_id"] for x in v2} == {"K1", "K2", "K3", "K4"}, "自己検証失敗"
    print("checker自己検証OK")
