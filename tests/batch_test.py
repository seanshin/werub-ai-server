#!/usr/bin/env python3
"""WeRU.B 실전 배치 테스트 — 5가지 AI 작업 시나리오"""

import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from client import OllamaBatch

BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:8585")
client = OllamaBatch(BASE_URL)

# ============================================================================
# 테스트 데이터 (WeRU.B 실제 서비스 시나리오)
# ============================================================================

# 1. 게시글 요약 — 카페 게시글을 1~2줄로 요약
POSTS = [
    "안녕하세요, 오늘 카페에서 진행하는 독서 모임에 대해 공지드립니다. 매주 수요일 저녁 7시에 온라인으로 진행하며, 이번 달 책은 '채식주의자'입니다. 참여를 원하시는 분은 댓글로 신청해 주세요. 첫 참여자도 환영합니다!",
    "프론트엔드 개발자 구인합니다. React/Next.js 경험 3년 이상, TypeScript 필수입니다. 리모트 근무 가능하며, 연봉은 협의합니다. 관심있으신 분은 DM 주세요. 포트폴리오 필수입니다.",
    "주말에 서울숲 근처에서 플로깅 모임 합니다! 4월 5일 토요일 오전 10시, 서울숲 2번 출구 앞 집합입니다. 장갑과 쓰레기봉투는 준비됩니다. 참가비 무료이고, 끝나고 점심도 같이 먹어요.",
    "Next.js 16에서 App Router의 params가 Promise로 바뀌었는데, 기존 코드 마이그레이션 방법 공유합니다. props.params를 await로 받아야 하고, generateStaticParams도 async로 변경 필요합니다.",
    "오늘 만든 된장찌개 레시피 공유합니다. 재료: 된장 2큰술, 두부 반모, 호박 1/3개, 청양고추 1개, 대파. 물 500ml에 된장을 풀고, 야채를 넣어 15분 끓이면 됩니다.",
]

# 2. 자동 태깅 — 게시글에 적합한 태그 3~5개 생성
# (위 POSTS 재사용)

# 3. 감성 분석 — 리뷰/댓글의 긍정/부정/중립 판별
COMMENTS = [
    "이 카페 정말 좋아요! 분위기도 좋고 사람들도 친절해서 매번 방문합니다.",
    "글 내용이 부실하고 광고 같아서 별로예요. 시간 낭비했습니다.",
    "오늘 날씨가 흐리네요. 비가 올 수도 있겠어요.",
    "가입한지 한 달 됐는데 커뮤니티 활동이 활발해서 만족합니다. 특히 Q&A 게시판이 유용해요.",
    "배송이 너무 늦어서 취소하고 싶은데 방법을 모르겠어요. 고객센터 연결도 안 되고 답답합니다.",
]

# 4. 번역 — 한국어 → 영어
TRANSLATE_KR = [
    "위루비는 카페 기반 커뮤니티 플랫폼입니다.",
    "매주 수요일 저녁에 온라인 독서 모임을 진행합니다.",
    "개인 워크스페이스에서 위키, 자료실, 캘린더를 관리할 수 있습니다.",
]

# 5. Q&A 답변 보조 — 기술 질문에 대한 답변 생성
QUESTIONS = [
    "Next.js에서 서버 컴포넌트와 클라이언트 컴포넌트의 차이점이 뭔가요?",
    "Prisma에서 relation을 가진 모델의 cascade delete는 어떻게 설정하나요?",
    "Redis를 세션 저장소로 사용할 때 장점과 단점은 무엇인가요?",
]

# ============================================================================
# 테스트 실행
# ============================================================================

def header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def run_test_1_summary():
    """게시글 요약"""
    header("TEST 1: 게시글 요약 (qwen2.5:7b)")
    batch = client.batch("qwen2.5:7b", [
        {"prompt": f"다음 게시글을 한국어 1~2문장으로 요약해주세요. 제목 형식으로 간결하게.\n\n{post}"}
        for post in POSTS
    ], system="당신은 한국어 커뮤니티 게시글 요약 전문가입니다. 핵심만 간결하게 요약합니다.",
       options={"temperature": 0.3, "num_predict": 50})
    print(f"  Batch: {batch.id[:8]} ({batch.count} jobs)")
    result = batch.wait(timeout=120)
    print(f"  Completed: {result['completed']}/{result['total']}")
    for i, j in enumerate(result['jobs']):
        print(f"\n  [{i+1}] 원문: {POSTS[i][:40]}...")
        print(f"      요약: {j.get('response','ERROR')[:80]}")
        print(f"      ({j.get('tokens',0)} tokens, {(j.get('duration_ms',0)/1000):.1f}s)")
    return result

def run_test_2_tagging():
    """자동 태깅"""
    header("TEST 2: 자동 태깅 (llama3.2:3b)")
    batch = client.batch("llama3.2:3b", [
        {"prompt": f"다음 게시글에 적합한 태그를 3~5개 생성해주세요. 형식: #태그1 #태그2 ...\n\n{post}"}
        for post in POSTS
    ], system="한국어 태그만 생성. 짧고 명확하게.",
       options={"temperature": 0.5, "num_predict": 30})
    print(f"  Batch: {batch.id[:8]} ({batch.count} jobs)")
    result = batch.wait(timeout=60)
    print(f"  Completed: {result['completed']}/{result['total']}")
    for i, j in enumerate(result['jobs']):
        print(f"  [{i+1}] {j.get('response','ERROR')[:60]}")
    return result

def run_test_3_sentiment():
    """감성 분석"""
    header("TEST 3: 감성 분석 (llama3.2:3b)")
    batch = client.batch("llama3.2:3b", [
        {"prompt": f"다음 댓글의 감성을 분석하세요. 반드시 '긍정', '부정', '중립' 중 하나만 답하세요.\n\n{c}"}
        for c in COMMENTS
    ], system="감성 분석기. 한 단어로만 답하세요: 긍정, 부정, 중립",
       options={"temperature": 0.1, "num_predict": 10})
    print(f"  Batch: {batch.id[:8]} ({batch.count} jobs)")
    result = batch.wait(timeout=60)
    print(f"  Completed: {result['completed']}/{result['total']}")
    expected = ["긍정", "부정", "중립", "긍정", "부정"]
    for i, j in enumerate(result['jobs']):
        resp = j.get('response','').strip()[:10]
        exp = expected[i]
        match = "OK" if exp in resp else "MISS"
        print(f"  [{i+1}] {match:4s} expected={exp} got={resp}")
    return result

def run_test_4_translate():
    """번역"""
    header("TEST 4: 한→영 번역 (qwen2.5:7b)")
    batch = client.batch("qwen2.5:7b", [
        {"prompt": f"Translate to English:\n{text}"}
        for text in TRANSLATE_KR
    ], options={"temperature": 0.3, "num_predict": 60})
    print(f"  Batch: {batch.id[:8]} ({batch.count} jobs)")
    result = batch.wait(timeout=60)
    print(f"  Completed: {result['completed']}/{result['total']}")
    for i, j in enumerate(result['jobs']):
        print(f"  [{i+1}] KR: {TRANSLATE_KR[i]}")
        print(f"       EN: {j.get('response','ERROR')[:80]}")
    return result

def run_test_5_qa():
    """Q&A 답변"""
    header("TEST 5: Q&A 답변 (qwen2.5:14b)")
    batch = client.batch("qwen2.5:14b", [
        {"prompt": q}
        for q in QUESTIONS
    ], system="당신은 시니어 풀스택 개발자입니다. 한국어로 간결하고 정확하게 답변합니다. 3~5문장으로.",
       options={"temperature": 0.5, "num_predict": 150})
    print(f"  Batch: {batch.id[:8]} ({batch.count} jobs)")
    result = batch.wait(timeout=300)
    print(f"  Completed: {result['completed']}/{result['total']}")
    for i, j in enumerate(result['jobs']):
        print(f"\n  Q: {QUESTIONS[i]}")
        print(f"  A: {j.get('response','ERROR')[:200]}")
        print(f"     ({j.get('tokens',0)} tokens, {(j.get('duration_ms',0)/1000):.1f}s)")
    return result

# ============================================================================
# 결과 리포트
# ============================================================================

if __name__ == "__main__":
    print("WeRU.B 실전 배치 테스트")
    print(f"Server: {BASE_URL}")
    print(f"Status: {client.status()}")

    start = time.time()
    results = []

    results.append(("요약", run_test_1_summary()))
    results.append(("태깅", run_test_2_tagging()))
    results.append(("감성분석", run_test_3_sentiment()))
    results.append(("번역", run_test_4_translate()))
    results.append(("Q&A", run_test_5_qa()))

    elapsed = time.time() - start

    header("SUMMARY")
    total_jobs = 0
    total_tokens = 0
    total_ms = 0
    for name, r in results:
        jobs = r.get('jobs', [])
        tokens = sum(j.get('tokens', 0) for j in jobs)
        ms = sum(j.get('duration_ms', 0) for j in jobs)
        completed = r.get('completed', 0)
        failed = r.get('failed', 0)
        total_jobs += len(jobs)
        total_tokens += tokens
        total_ms += ms
        print(f"  {name:8s}  {completed}/{len(jobs)} done  {tokens:4d} tokens  {ms/1000:.1f}s  {'PASS' if failed==0 else f'FAIL({failed})'}")

    avg_speed = total_tokens / max(total_ms / 1000, 0.01)
    print(f"\n  Total: {total_jobs} jobs, {total_tokens} tokens, {total_ms/1000:.1f}s processing")
    print(f"  Avg Speed: {avg_speed:.1f} tok/s")
    print(f"  Wall Time: {elapsed:.1f}s")
    print(f"\n  Stats: {json.dumps(client.stats().get('jobs_per_model',{}), indent=2, ensure_ascii=False)}")
