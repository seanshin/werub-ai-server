"""Swagger 독스트링 YAML 파싱 가드 — openapi.json 500 재발 차단.

`/openapi.json`·`/apispec.json`·`/docs`는 외부 연동의 셀프서비스 정본이라 한 라우트의
독스트링 YAML이 깨지면 **스펙 전체가 500**이 된다(2026-07-27 v2.124.39에서 실제 발생:
flow mapping 안에 따옴표 없는 `{id, text, metadata}`가 들어가 하루 동안 스펙 전면 중단).
서버 없이 순수 파싱만으로 같은 클래스를 잡는다.
"""
import glob
import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _swagger_blocks(path):
    """파일에서 (함수 독스트링의 '---' 이후 YAML 본문) 목록을 뽑는다."""
    import ast

    with open(path, encoding="utf-8") as fh:
        try:
            tree = ast.parse(fh.read())
        except SyntaxError:
            return []
    blocks = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node)
        if not doc or "\n---" not in doc:
            continue
        body = doc.split("\n---", 1)[1]
        if body.strip():
            blocks.append((node.name, body))
    return blocks


ROUTE_FILES = sorted(glob.glob(os.path.join(ROOT, "routes", "*.py")))


@pytest.mark.parametrize("path", ROUTE_FILES, ids=lambda p: os.path.basename(p))
def test_swagger_docstring_yaml_parses(path):
    for func_name, body in _swagger_blocks(path):
        try:
            yaml.safe_load(body)
        except yaml.YAMLError as e:
            pytest.fail(
                f"{os.path.basename(path)}::{func_name} 의 Swagger YAML 파싱 실패 "
                f"→ /openapi.json 전체가 500이 된다.\n{e}"
            )
