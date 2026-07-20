# SkillGate Phase 2: Claude Code フックアダプタ（設計図§3 差し込み口(b)）
# UserPromptSubmit フックとして動く。stdinのJSONからプロンプトを取り、
# ルーター（stage1決定論＋stage2小型LLM）で該当スキルを決定し、
# additionalContext としてスキル本文を注入する。
#
# 設計原則:
#   1. フェイルオープン: 例外・タイムアウト・Ollama停止でも exit 0 / 注入なし。
#      本体の会話を絶対に止めない（誤爆側の害の無害化＝M-005二段構え）。
#   2. 縮退運転: stage2不能時はstage1（語彙一致・決定論）のみで動く。
#   3. 監査ログ: 全判断を hook_log.jsonl に1行JSON追記（規約§3。ログに無い動作は存在しない）。
#   4. 注入上限: max_inject_chars 超過分は切る（③処理カロリー対策。切った事実もログに残す）。
#
# 導入: README_フック導入.md を参照。設定は hook_config.json。
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent            # SkillGate/router/
sys.path.insert(0, str(HERE))
from route import stage1, stage2, render_skill_list  # noqa: E402

DEFAULT_CONFIG = {
    "skills_dir": str(HERE.parent / "corpus"),  # real/synth両方を拾う。実運用では自分のスキル置き場に変更
    "use_stage2": True,
    "stage2_model": "qwen3:8b",
    "stage2_timeout_sec": 30,
    "prompt_version": "v2",
    "max_inject_chars": 20000,
    "log_file": str(HERE / "hook_log.jsonl"),
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    f = HERE / "hook_config.json"
    if f.is_file():
        try:
            cfg.update(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass  # 設定が壊れていても既定値で続行（フェイルオープン）
    return cfg


def parse_skill(path: Path) -> dict:
    """SKILL.mdをその場で解析（bench/build_index.pyと同ロジックの軽量版）。"""
    text = path.read_text(encoding="utf-8")
    name, desc, body = None, None, text
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if m:
        fm, body = m.group(1), text[m.end():]
        nm = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        name = nm.group(1).strip() if nm else None
        dm = re.search(r"^description:\s*(.+?)(?=^\w+:|\Z)", fm, re.MULTILINE | re.DOTALL)
        if dm:
            desc = " ".join(x.strip() for x in dm.group(1).strip().splitlines())
    sections = [{"level": len(h.group(1)), "title": h.group(2).strip()}
                for h in re.finditer(r"^(#{1,4})\s+(.+)$", body, re.MULTILINE)]
    return {"id": path.parent.name, "name": name or path.parent.name,
            "description": desc or "", "sections": sections, "_path": path}


def load_skills(skills_dir: str) -> list:
    """skills_dir配下の */SKILL.md（1階層 or 2階層）を全部拾う。"""
    root = Path(skills_dir)
    found = sorted(set(list(root.glob("*/SKILL.md")) + list(root.glob("*/*/SKILL.md"))))
    return [parse_skill(f) for f in found]


def load_triggers() -> dict:
    f = HERE / "triggers.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))["triggers"]
    except Exception:
        return {}  # トリガー無しでもstage2だけで動く


def build_injection(decision_skills: list, sections: dict, skills: list, max_chars: int) -> tuple:
    by_id = {s["id"]: s for s in skills}
    parts, truncated = [], False
    for sid in decision_skills:
        s = by_id.get(sid)
        if not s:
            continue
        body = s["_path"].read_text(encoding="utf-8")
        sec = sections.get(sid)
        if sec:
            m = re.search(rf"^#+\s*{re.escape(sec)}\s*$(.*?)(?=^#+\s|\Z)",
                          body, re.MULTILINE | re.DOTALL)
            if m:
                body = f"## {sec}\n{m.group(1).strip()}"
        parts.append(f"<skill id=\"{sid}\">\n{body}\n</skill>")
    text = ("以下は今回の依頼に関係する可能性があるスキル文書。関係する場合は必ずこの手順に従うこと。"
            "関係しない場合は無視してよい。\n" + "\n".join(parts)) if parts else ""
    if len(text) > max_chars:
        text, truncated = text[:max_chars] + "\n…（注入上限で切り詰め）", True
    return text, truncated


def audit(cfg: dict, record: dict) -> None:
    try:
        record["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(cfg["log_file"], "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # ログ失敗でも本体は止めない


def main() -> int:
    cfg = load_config()
    rec = {"stage1": [], "stage2": [], "final": [], "mode": "none", "error": None}
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        prompt = payload.get("prompt") or ""
        if not prompt.strip():
            audit(cfg, rec)
            return 0
        rec["prompt_head"] = prompt[:120]
        skills = load_skills(cfg["skills_dir"])
        if not skills:
            rec["error"] = "skills_dirにSKILL.mdなし"
            audit(cfg, rec)
            return 0
        triggers = load_triggers()
        ids = {s["id"] for s in skills}
        s1 = stage1(prompt, triggers, ids)
        rec["stage1"] = s1
        final, sections, mode = sorted(s1), {}, "stage1のみ"
        if cfg["use_stage2"]:
            try:
                s2 = stage2(prompt, skills, model=cfg["stage2_model"],
                            timeout=cfg["stage2_timeout_sec"],
                            prompt_version=cfg.get("prompt_version", "v2"))
                if not s2.get("parse_error"):
                    final, sections, mode = sorted(set(s2["skills"])), s2["sections"], "stage2主"
                    rec["stage2"] = s2["skills"]
            except Exception as e:
                rec["error"] = f"stage2失敗→stage1縮退: {type(e).__name__}"
        rec["final"], rec["mode"] = final, mode
        if not final:
            audit(cfg, rec)
            return 0
        text, truncated = build_injection(final, sections, skills, cfg["max_inject_chars"])
        rec["injected_chars"], rec["truncated"] = len(text), truncated
        audit(cfg, rec)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text}}, ensure_ascii=False))
        return 0
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        audit(cfg, rec)
        return 0  # フェイルオープン。非0を返さない


if __name__ == "__main__":
    sys.exit(main())
