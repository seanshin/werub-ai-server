#!/usr/bin/env python3
"""한국어 경로 CJK 표류 전수 측정 — 사전 등록 `docs/01-plan/references/korean-drift-survey-2026-09-28.md`.

라이브 HTTP(localhost)로 경로별 N회 **순차** 호출하고, 각 요청의 시간창에 난
`[korean-filter] CJK 표류 입력` 경고(v2.125.53 관측 로그)를 journald에서 세어 표류 요청률을 낸다.
앱을 2중 기동하지 않는다(스케줄러 동시 기동 → 라이브와 ChromaDB 동시 쓰기 위험).

사용: ./venv/bin/python scripts/korean_drift_survey.py [--n 10] [--only P1,P3] [--out logs/korean_drift_survey.json]
"""
import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime

import requests

BASE = "http://localhost:8585"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⚠️ 1차 실행(2026-09-28) 교훈: 5개 입력을 2회 순환하면 2회차가 **전부 캐시 히트**였고, 게이트·평가에서
# 이미 물은 통제군 질문은 1회차부터 캐시였다(P1·P5 10/10이 0.0s) — 생성을 재지 못했다.
# ⇒ 경로마다 **서로 다른 10개**, 통제군과 겹치지 않는 문장. 1초 미만 응답은 캐시로 보고 분모에서 뺀다.
MEDLAW_QS = ["의료기관 명칭 표시 규정은 어떻게 되는가?", "진단서를 발급할 수 있는 사람은 누구인가?",
             "의료인의 면허 취소 사유에는 무엇이 있는가?", "환자의 비밀 누설 금지 의무는 어디에 규정되어 있는가?",
             "당직 의료인 배치 기준은 어떻게 되는가?", "의료기관 휴업 시 신고 의무가 있는가?",
             "의료인이 아닌 자가 의료광고를 할 수 있는가?", "처방전 보존 기간과 보존 주체는?",
             "의료기관 인증 제도의 대상은 무엇인가?", "비급여 진료비용 고지 의무는 어떻게 되는가?"]
COUNSEL = ["회사 일이 많아서 요즘 스트레스를 많이 받고 있어요.", "친구와 다툰 뒤로 마음이 불편해요.",
           "시험을 앞두고 잠이 잘 안 와요.", "이사 온 뒤로 외로움을 많이 느껴요.", "부모님과 대화가 자꾸 어긋나요.",
           "새 팀에 적응하기가 어려워요.", "요즘 아무것도 하기 싫어요.", "연애가 끝나고 마음이 허전해요.",
           "발표할 때마다 너무 긴장돼요.", "진로를 정하지 못해 불안해요."]
DRAFTS = ["봄날 산책에 대한 짧은 수필을 써 줘.", "새해 다짐을 담은 짧은 글을 써 줘.",
          "동네 도서관을 소개하는 짧은 글을 써 줘.", "비 오는 날의 출근길을 짧게 묘사해 줘.",
          "처음 요리를 해 본 경험을 짧게 써 줘.", "가을 운동회의 추억을 짧게 써 줘.",
          "반려식물을 키우는 즐거움에 대해 짧게 써 줘.", "지하철에서 본 풍경을 짧게 묘사해 줘.",
          "할머니의 손맛에 대한 짧은 글을 써 줘.", "첫 월급을 받은 날을 짧게 써 줘."]
POLISH = ["오늘은 날씨가 좋았다 그래서 산책을 했다 공원에는 사람이 많았다 기분이 좋았다.",
          "회의에서 논의된 내용은 다음과 같음 일정 조정 필요 예산 검토 필요 담당자 지정 필요.",
          "저희 병원은 환자분들의 편의를 위해서 주차장을 확장 했습니다 많은 이용 바랍니다.",
          "이번 프로젝트는 여러가지 어려움이 있었지만 팀원들 덕분에 잘 마무리 할수 있었다.",
          "고객님의 문의사항에 대해서 확인후 빠른시일내에 답변 드리도록 하겠습니다.",
          "내일 오전 열시 까지 보고서를 제출 해주시기 바랍니다 늦으면 안됩니다.",
          "우리 카페는 신선한 원두만을 사용 하며 매일 아침 직접 로스팅을 합니다.",
          "주민 여러분께 알려드립니다 다음주 월요일 단수가 예정 되어 있습니다.",
          "이 제품은 가볍고 튼튼해서 여행갈때 가지고 다니기에 아주 좋은것 같습니다.",
          "학생들은 수업시간에 휴대폰을 사용하지 말고 선생님 말씀에 집중 해야 합니다."]
GENERAL = ["주말에 가볼 만한 서울 근교 여행지를 추천해 줘.", "건강한 아침 식단 예시를 알려 줘.",
           "회의록을 잘 쓰는 요령을 알려 줘.", "초보자를 위한 달리기 시작 방법을 알려 줘.",
           "집중력을 높이는 방법을 알려 줘.", "겨울철 실내 습도 관리 요령을 알려 줘.",
           "좋은 이메일 제목을 쓰는 방법을 알려 줘.", "처음 캠핑 갈 때 준비물을 알려 줘.",
           "스마트폰 사용 시간을 줄이는 방법을 알려 줘.", "작은 방을 넓어 보이게 꾸미는 방법을 알려 줘."]
GENERAL2 = ["재택근무 효율을 높이는 방법을 알려 줘.", "책을 꾸준히 읽는 습관 만드는 법을 알려 줘.",
            "가계부를 쉽게 쓰는 방법을 알려 줘.", "봄철 알레르기 관리 요령을 알려 줘.",
            "면접에서 자기소개를 잘하는 방법을 알려 줘.", "화분 물주기 요령을 알려 줘.",
            "아이와 함께하기 좋은 실내 놀이를 알려 줘.", "좋은 수면 습관을 알려 줘.",
            "중고거래할 때 주의할 점을 알려 줘.", "여행 짐을 가볍게 싸는 방법을 알려 줘."]
TOPICS = ["고혈압", "당뇨병", "천식", "골다공증", "위식도역류질환",
          "갑상선기능저하증", "편두통", "통풍", "만성신장병", "대상포진"]

PATHS = {
    "P1": ("/api/medical-law/ask", lambda i: {"question": MEDLAW_QS[i]}),
    "P2": ("/api/counseling/chat", lambda i: {"message": COUNSEL[i]}),
    "P3": ("/api/writing/draft", lambda i: {"prompt": DRAFTS[i], "length": "short"}),
    "P4": ("/api/writing/polish", lambda i: {"text": POLISH[i]}),
    # ⚠️ /api/chat/enhanced는 medical/coding/writing/legal 도메인만 생성한다 — 일반 질문은 즉시 안내문(생성 0).
    #    1차 재실행에서 일반 질문 10개가 전부 0.0s 안내문이었다 → 도메인 명시 질문으로.
    "P5": ("/api/chat/enhanced", lambda i: {"messages": [{"role": "user", "content": ENHANCED_QS[i][1]}],
                                            "domain": ENHANCED_QS[i][0]}),
    "P6": ("/api/orchestrator/chat", lambda i: {"message": GENERAL[i]}),
    "P7": ("/api/medical/patient-education", lambda i: {"topic": TOPICS[i]}),
}
ENHANCED_QS = [("medical", "고혈압 환자가 일상에서 주의할 생활습관을 알려 줘."),
               ("medical", "당뇨병 환자의 발 관리 요령을 알려 줘."),
               ("medical", "감기와 독감의 차이를 알려 줘."),
               ("medical", "골다공증 예방을 위한 운동을 알려 줘."),
               ("medical", "역류성 식도염에 좋은 식습관을 알려 줘."),
               ("writing", "보고서 서론을 설득력 있게 쓰는 방법을 알려 줘."),
               ("writing", "사과 이메일을 정중하게 쓰는 요령을 알려 줘."),
               ("writing", "자기소개서 첫 문장을 인상적으로 쓰는 법을 알려 줘."),
               ("writing", "공지문을 간결하게 쓰는 방법을 알려 줘."),
               ("writing", "블로그 글 제목을 매력적으로 짓는 법을 알려 줘.")]
CACHE_S = 1.0   # 이보다 빠르면 캐시로 보고 분모 제외 — ⚠️ writing/polish처럼 원래 빠른 경로(0.3s 실생성)엔 부적합, 입력 신규성으로 판정

LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[WARNING\] \[korean-filter\] "
                    r"CJK 표류 입력 share=([\d.]+) len=(\d+) caller=(\S+)")


def drift_logs(since, until):
    out = subprocess.run(["journalctl", "-u", "ollama-admin", "--since", since, "--until", until,
                          "--no-pager", "-o", "cat"], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        m = LOG_RE.search(line)
        if m:
            rows.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp(),
                         float(m.group(2)), int(m.group(3)), m.group(4)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="경로당 입력 수(최대 10 — 서로 다른 입력)")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="logs/korean_drift_survey_20260928c.json")
    a = ap.parse_args()
    keys = [k for k in PATHS if not a.only or k in a.only.split(",")]
    result = {}
    for key in keys:
        path, payload = PATHS[key]
        reqs = []
        for i in range(a.n):
            t0 = time.time()
            try:
                r = requests.post(BASE + path, json=payload(i), timeout=300)
                status, size = r.status_code, len(r.content)
            except Exception as e:  # noqa: BLE001
                status, size = f"ERR {type(e).__name__}", 0
            t1 = time.time()
            reqs.append({"i": i, "t0": t0, "t1": t1, "status": status, "bytes": size})
            time.sleep(1.2)   # 로그 시각(초 단위) 경계 분리
        since = datetime.fromtimestamp(reqs[0]["t0"] - 1).strftime("%Y-%m-%d %H:%M:%S")
        until = datetime.fromtimestamp(reqs[-1]["t1"] + 2).strftime("%Y-%m-%d %H:%M:%S")
        logs = drift_logs(since, until)
        for rq in reqs:
            hits = [l for l in logs if rq["t0"] - 1 <= l[0] <= rq["t1"] + 1]
            rq["drift"] = bool(hits)
            rq["hits"] = [{"share": h[1], "len": h[2], "caller": h[3]} for h in hits]
        gen = [r for r in reqs if (r["t1"] - r["t0"]) >= CACHE_S]
        n_drift = sum(r["drift"] for r in gen)
        callers = sorted({h["caller"] for r in reqs for h in r["hits"]})
        med = sorted(r["t1"] - r["t0"] for r in reqs)[len(reqs) // 2]
        result[key] = {"path": path, "n": a.n, "generated": len(gen), "drift_requests": n_drift,
                       "status": sorted({str(r["status"]) for r in reqs}),
                       "max_share": max((h["share"] for r in reqs for h in r["hits"]), default=0.0),
                       "callers": callers, "median_s": round(med, 1), "requests": reqs}
        print(f"{key} {path:34s} drift {n_drift}/{len(gen)} (생성 {len(gen)}/{a.n})  status={result[key]['status']} "
              f"max_share={result[key]['max_share']:.3f} median={med:.1f}s callers={callers}", flush=True)
    json.dump(result, open(os.path.join(ROOT, a.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("→", a.out)


if __name__ == "__main__":
    main()
