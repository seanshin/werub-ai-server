#!/usr/bin/env python3
"""전체 .py 문법 검사 (ast.parse) — pre-push 훅용, 외부 의존 0건.

- 클라우드/GitHub Actions 없이 로컬에서 push 직전 실행 → 비용 $0
- 문법 오류 1건이라도 있으면 exit 1 (push 차단)
- requirements.txt 존재도 확인

직접 실행: python3 scripts/check_syntax.py
"""
import ast
import pathlib
import sys

SKIP_PARTS = {".git", "__pycache__", "venv", ".venv", "node_modules"}


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent

    if not (root / "requirements.txt").exists():
        print("✗ requirements.txt 없음", file=sys.stderr)
        return 1

    errors = []
    n = 0
    for p in root.rglob("*.py"):
        if any(part in SKIP_PARTS for part in p.parts):
            continue
        n += 1
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append(f"{p.relative_to(root)}:{e.lineno}: {e.msg}")

    if errors:
        print(f"✗ 문법 오류 {len(errors)}건 (push 차단):", file=sys.stderr)
        for e in errors:
            print(f"    {e}", file=sys.stderr)
        return 1

    print(f"✓ 문법 검사 통과 ({n}개 .py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
