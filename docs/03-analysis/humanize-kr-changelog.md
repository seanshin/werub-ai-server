# im-not-ai (Humanize KR) 업데이트 추적 로그

**추적 대상**: [epoko77-ai/im-not-ai](https://github.com/epoko77-ai/im-not-ai)
**마지막 확인**: 2026-06-01
**마지막 확인 버전**: v2.0.0
**마지막 커밋일**: 2026-05-07

---

## 추적 항목

| 파일 | 추적 목적 | 현재 상태 |
|------|----------|----------|
| `references/ai-tell-taxonomy.md` | 패턴 추가/보강 | v2.0: 10대×60+패턴 |
| `references/metrics.py` | 메트릭 함수 변경 | v1.6: 8함수 |
| `references/metrics_v2.py` | post-editese 메트릭 | v2.0: 14함수 |
| `references/quick-rules.md` | S1/S2 규칙 변경 | v2.0: S1 18종 |
| `references/baseline.json` | 코퍼스 데이터 갱신 | KatFish v1.6 |
| `references/baseline_v2.json` | v2.0 baseline | 초기 |
| `references/scholarship.md` | 학술 참조 추가 | 29학자 |

---

## 확인 명령어

```bash
# 최신 릴리스 확인
gh api repos/epoko77-ai/im-not-ai/releases --jq '.[0]'

# 최근 5 커밋
gh api repos/epoko77-ai/im-not-ai/commits --jq '.[0:5] | .[] | "\(.sha[0:7]) \(.commit.message | split("\n")[0])"'

# 특정 파일 변경 확인
gh api repos/epoko77-ai/im-not-ai/commits?path=.claude/skills/humanize-korean/references/metrics.py --jq '.[0]'
```

---

## 변경 이력

### 2026-06-01 — 최초 분석

- **버전**: v2.0.0
- **상태**: 초기 분석 완료
- **보고서**: `docs/03-analysis/humanize-kr-analysis.md`
- **도입 결정**: Phase 1 (metrics + rules + baseline 이식) 승인 대기

---

*다음 확인 예정: 2026-07-01*
