#!/usr/bin/env bash
# openCARP 설치 PoC (P3b) — 심장 EP 대리모델 학습 ground-truth 생성기 실증.
#
# 대상: GPU 서버(Ubuntu 24.04, weruby). openCARP = CPU/MPI 솔버라 GPU/Ollama 무경합(비침습).
# 격리: Docker 공식 이미지(호스트 무오염·제거 용이). 결과는 ~/opencarp_poc 에 캡처.
# 검증됨(2026-06-21): 단일세포 EP(bench) + 조직 monodomain → LAT 활성화맵 생성.
#
# 사용: bash scripts/opencarp_poc.sh
# 제거: docker rmi docker.opencarp.org/opencarp/opencarp:latest ; rm -rf ~/opencarp_poc
set -euo pipefail

IMG="docker.opencarp.org/opencarp/opencarp:latest"
WORK="$HOME/opencarp_poc"
mkdir -p "$WORK"

echo "== 1. openCARP 공식 이미지(~2.35GB, CPU/MPI) =="
docker pull "$IMG"

echo "== 2. 단일세포 EP 실증 (tenTusscherPanfilov 인간 심실 이온모델, 300ms AP) =="
docker run --rm -v "$WORK":/work "$IMG" bash -lc \
  "cd /work && bench --imp tenTusscherPanfilov --duration 300 --dt 0.01 \
     --stim-curr 30 --stim-dur 2 --fout=/work/singlecell && ls /work/singlecell.Vm.bin"

echo "== 3. 튜토리얼 실험(조직 EP) =="
[ -d "$WORK/experiments" ] || git clone --depth 1 \
  https://git.opencarp.org/openCARP/experiments.git "$WORK/experiments"

echo "== 4. 조직 monodomain 시뮬 → LAT(국소활성화시간) ground-truth =="
docker run --rm -v "$WORK":/work \
  -w /work/experiments/examples/02_EP_tissue/00_simple "$IMG" \
  bash -lc "python3 run.py --np 4 --ID /work/sim_simple"
# 산출: sim_simple/vm.igb (막전위장 노드x시간) + init_acts_activation-thresh.dat (LAT/노드)

echo "== 5. LAT 통계(파면 전파 입증) =="
docker run --rm -v "$WORK":/work "$IMG" bash -lc "python3 - <<'PY'
import numpy as np
v=[]
for ln in open('/work/sim_simple/init_acts_activation-thresh.dat'):
    for t in ln.split():
        try: v.append(float(t))
        except ValueError: pass
a=np.array(v); a=a[a>=0]
print('LAT nodes:%d  min/max/mean=%.2f/%.2f/%.2f ms  spread=%.2f'%(
      a.size,a.min(),a.max(),a.mean(),a.max()-a.min()))
PY"

echo "== PoC 완료 — 메시→LAT ground-truth 생성 검증. 다음(P3c): 파라미터 스윕 데이터셋 + 신경연산자 학습. =="
