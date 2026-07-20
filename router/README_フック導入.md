# SkillGateルーター Claude Codeフック導入手順

## 何が起きるようになるか

Claude Codeで何かを送信するたびに、裏でルーター（stage1語彙一致＋stage2小型LLM分類）が走り、該当するスキルの本文が自動でプロンプトに添付される。**モデルがスキルを「開くかどうか」を判断しなくなる**＝サボり（開き忘れ）が構造的に起きない。

実測根拠：素のqwen3はスキル20本でサボり率10〜47%。本ルーターは見逃し0%（初代A群30問＋新問A2の30問、計60問で確認済み・2026-07-05）。

## 前提

- Python 3.x（`python`または`py`で起動できること）
- stage2を使う場合：Ollamaが起動していて`qwen3:8b`が入っていること
  （**Ollamaが落ちていても壊れない**。stage1のみの縮退運転になるだけ）

## 導入（2ステップ）

### 1. 設定ファイル（任意）

`router/hook_config.json`を作る（無ければ既定値で動く）：

```json
{
  "skills_dir": "<あなたのスキル置き場（例: ~/.claude/skills）>",
  "use_stage2": true,
  "stage2_model": "qwen3:8b",
  "stage2_timeout_sec": 30,
  "max_inject_chars": 20000
}
```

- `skills_dir`：自分のスキル置き場。配下の`*/SKILL.md`（2階層まで）を自動で拾う。
- 既定値はSkillGateのベンチ用corpus（動作確認用）。**実運用では必ず自分のスキル置き場に変える**。

### 2. Claude Codeのフック登録

`.claude/settings.json`（プロジェクト用）または`~/.claude/settings.json`（全体用）に追記：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"<このリポジトリの絶対パス>/router/hook_adapter.py\""
          }
        ]
      }
    ]
  }
}
```

## 動作確認

コマンドプロンプトで：

```
echo {"prompt": "経費精算のチェックをして"} | python router\hook_adapter.py
```

→ `additionalContext`にkeihi-seisanの本文が入ったJSONが出れば成功。
→ 無関係な文（「今日は暑いね」）なら出力なし（正常）。

## 監査ログ

全判断は`router/hook_log.jsonl`に1行JSONで記録される（stage1/stage2の判断・最終注入・縮退の有無・エラー）。「ログに無い動作は存在しない」（規約§3）。

## 安全設計（何があっても会話を止めない）

- ルーター内のあらゆる失敗（設定破損・スキル不在・Ollama停止・タイムアウト）→**注入なしで正常終了**。Claude Codeは普段通り動く。
- stage2が使えない時はstage1（決定論）のみで動く縮退運転。
- 注入は20,000字で頭打ち（③処理カロリー対策）。切り詰めた事実もログに残る。

## 既知の限界

- ルーターの誤起動（不要なスキルを添付）は新問B群で6.7〜20%。ただし注入文には「関係しない場合は無視してよい」の無害化文言付き。
- 会話の文脈は見ない（依頼文単体で判断）。「さっきのアレやって」のような指示語だけの依頼は拾えないことがある。
