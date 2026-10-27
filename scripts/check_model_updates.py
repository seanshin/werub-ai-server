#!/usr/bin/env python3
"""설치된 Ollama 모델의 제자리 갱신 여부 + Ollama 바이너리 최신 여부 (v2.125.9 · 커밋본).

v2.125.5(2026-08-18)에서 /tmp 스크립트로 했던 조사를 레포에 고정한다. 그때 탐지기가 두 번 틀렸다:
  ① `Docker-Content-Digest` 헤더가 없어 원격이 전부 `?` → "37개 전부 갱신 필요"로 오판
     → **매니페스트 본문 sha256**과 로컬 `/api/tags` digest를 비교한다.
  ② `/tags/list`가 404 → "모델 없음"으로 읽을 뻔 → 여기선 tags/list를 쓰지 않는다.
🔑 자기검증: 일치 개수가 0이면 탐지기가 깨진 것이다(모델이 전부 낡았을 리 없다) → 종료 코드 2.

사용(GPU): ./venv/bin/python scripts/check_model_updates.py [--json]
  - 로컬: http://localhost:11434/api/tags (name, digest)
  - 원격: https://registry.ollama.ai/v2/<ns>/<repo>/manifests/<tag> 본문 sha256
  - 제외: hf.co/* (HF 호스팅) · 레지스트리에 없는 자체 파인튜닝(werub*) → "확인 불가"로 분리 집계
  - Ollama: https://api.github.com/repos/ollama/ollama/releases/latest tag ↔ `ollama --version`
"""
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request

OLLAMA = "http://localhost:11434"
REGISTRY = "https://registry.ollama.ai"
UA = "werub-model-check/1.0"


def _get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def local_models():
    _, body = _get(OLLAMA + "/api/tags", timeout=10)
    return [(m["name"], m.get("digest") or "") for m in json.loads(body).get("models", [])]


def split_ref(name):
    """'qwen2.5:14b' → ('library','qwen2.5','14b') · 'user/repo:tag' → ('user','repo','tag')."""
    if name.startswith("hf.co/"):
        return None
    repo, _, tag = name.partition(":")
    tag = tag or "latest"
    if "/" in repo:
        ns, _, repo = repo.partition("/")
    else:
        ns = "library"
    return ns, repo, tag


def remote_manifest_sha(ns, repo, tag):
    url = "%s/v2/%s/%s/manifests/%s" % (REGISTRY, ns, repo, tag)
    try:
        status, body = _get(url, {"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:60]
    return hashlib.sha256(body).hexdigest(), None


def ollama_versions():
    try:
        out = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=10).stdout
        local = out.strip().split()[-1]
    except Exception:  # noqa: BLE001
        local = "?"
    try:
        _, body = _get("https://api.github.com/repos/ollama/ollama/releases/latest")
        j = json.loads(body)
        latest, published = j.get("tag_name", "?").lstrip("v"), (j.get("published_at") or "")[:10]
    except Exception as e:  # noqa: BLE001
        latest, published = "? (%s)" % str(e)[:40], ""
    return local, latest, published


def main():
    as_json = "--json" in sys.argv
    rows, unknown = [], []
    for name, digest in local_models():
        ref = split_ref(name)
        if ref is None:
            unknown.append((name, "hf.co 호스팅"))
            continue
        sha, err = remote_manifest_sha(*ref)
        if sha is None:
            unknown.append((name, err))
            continue
        rows.append((name, digest == sha, digest[:12], sha[:12]))
    same = [r for r in rows if r[1]]
    stale = [r for r in rows if not r[1]]
    local_v, latest_v, published = ollama_versions()
    result = {"checked": len(rows), "up_to_date": len(same), "stale": [r[0] for r in stale],
              "unverifiable": unknown, "ollama": {"local": local_v, "latest": latest_v,
                                                  "published": published}}
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print("=== Ollama 모델 제자리 갱신 (레지스트리 매니페스트 sha256 ↔ 로컬 digest) ===")
        for name, ok, ld, rd in rows:
            print("  %-46s %s  local=%s remote=%s" % (name, "최신" if ok else "🔴 갱신 있음", ld, rd))
        for name, why in unknown:
            print("  %-46s (확인 불가: %s)" % (name, why))
        print("→ %d/%d 최신 · 갱신 %d · 확인 불가 %d" % (len(same), len(rows), len(stale), len(unknown)))
        print("=== Ollama 바이너리: local %s ↔ latest %s (%s) %s" % (
            local_v, latest_v, published, "✅" if local_v == latest_v else "🔴 새 버전"))
    if rows and not same:
        print("🔴 자기검증 실패: 일치 0 — 비교 방법이 깨졌다(v2.125.5 ①). 결과를 믿지 말 것.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
