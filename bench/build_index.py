# SkillGate: corpus索引ビルダー
# corpus/{real,synth}/*/SKILL.md を走査し、frontmatter(name/description)と
# 見出し索引を抽出して corpus/index.json に書く。素Python・依存なし。
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # SkillGate/
CORPUS = ROOT / "corpus"


def parse_skill(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    name, desc = None, None
    body = text
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if m:
        fm = m.group(1)
        body = text[m.end():]
        nm = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        if nm:
            name = nm.group(1).strip()
        dm = re.search(r"^description:\s*(.+?)(?=^\w+:|\Z)", fm, re.MULTILINE | re.DOTALL)
        if dm:
            desc = " ".join(line.strip() for line in dm.group(1).strip().splitlines())
    sections = []
    for i, line in enumerate(body.splitlines(), 1):
        h = re.match(r"^(#{1,4})\s+(.+)$", line)
        if h:
            sections.append({"level": len(h.group(1)), "title": h.group(2).strip(), "line": i})
    return {
        "id": path.parent.name,
        "name": name or path.parent.name,
        "description": desc or "",
        "source": path.parent.parent.name,  # real / synth
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "lines": len(body.splitlines()),
        "sections": sections,
    }


def main() -> int:
    skills = []
    for group in ("real", "synth"):
        gdir = CORPUS / group
        if not gdir.is_dir():
            print(f"WARN: {gdir} なし", file=sys.stderr)
            continue
        for sk in sorted(gdir.iterdir()):
            f = sk / "SKILL.md"
            if f.is_file():
                skills.append(parse_skill(f))
    out = {"built_from": "corpus/{real,synth}", "count": len(skills), "skills": skills}
    (CORPUS / "index.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK: {len(skills)}本 → corpus/index.json")
    for s in skills:
        print(f"  {s['source']}/{s['id']}: {s['lines']}行, 見出し{len(s['sections'])}, desc {len(s['description'])}字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
