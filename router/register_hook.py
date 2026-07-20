# SkillGateルーターのClaude Codeフック登録（冪等・既存設定は壊さない）
# 1. %USERPROFILE%\.claude\settings.json にUserPromptSubmitフックをマージ（既存あればバックアップ）
# 2. router/hook_config.json を作成（skills_dir=corpus/real）
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # SkillGate/router/
ADAPTER = HERE / "hook_adapter.py"
CLAUDE_DIR = Path.home() / ".claude"
SETTINGS = CLAUDE_DIR / "settings.json"

HOOK_CMD = f'python "{ADAPTER}"'


def main() -> int:
    # --- settings.json マージ ---
    CLAUDE_DIR.mkdir(exist_ok=True)
    settings = {}
    if SETTINGS.is_file():
        backup = SETTINGS.with_name(f"settings.json.bak_{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
        print(f"既存settings.jsonをバックアップ: {backup.name}")
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    hooks = settings.setdefault("hooks", {})
    ups = hooks.setdefault("UserPromptSubmit", [])
    already = any(
        h.get("command") == HOOK_CMD
        for entry in ups for h in entry.get("hooks", [])
    )
    if already:
        print("登録済み（変更なし）")
    else:
        ups.append({"hooks": [{"type": "command", "command": HOOK_CMD}]})
        SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"フック登録完了: {SETTINGS}")
    # --- hook_config.json ---
    cfg_file = HERE / "hook_config.json"
    if not cfg_file.is_file():
        cfg = {
            "skills_dir": str(HERE.parent / "corpus" / "real"),
            "use_stage2": True,
            "stage2_model": "qwen3:8b",
            "stage2_timeout_sec": 30,
            "prompt_version": "v2",
            "max_inject_chars": 20000,
        }
        cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"hook_config.json作成（skills_dir=corpus/real）")
    else:
        print("hook_config.jsonは既存（変更なし）")
    # --- 検証 ---
    loaded = json.loads(SETTINGS.read_text(encoding="utf-8"))
    ok = any(h.get("command") == HOOK_CMD
             for e in loaded.get("hooks", {}).get("UserPromptSubmit", [])
             for h in e.get("hooks", []))
    print("機械検証:", "OK（フックが読める形で存在）" if ok else "NG")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
