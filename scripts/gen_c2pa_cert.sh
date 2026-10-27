#!/usr/bin/env bash
# C2PA 서명용 self-hosted 인증서 체인 생성 (Root CA → end-entity signing cert).
# 외부 CA 불필요. c2pa-rs 요구사항: end-entity(비 self-signed) + EKU=emailProtection + EC P-256 PKCS#8.
#
# 사용:  bash scripts/gen_c2pa_cert.sh [출력디렉터리]
# 기본 출력: ./secrets/c2pa  (key.pem/chain.pem — .gitignore로 커밋 제외)
# 생성 후 config.json:  "ai_marking_c2pa": true,
#   "c2pa_cert_path": "<repo>/secrets/c2pa/chain.pem",
#   "c2pa_key_path":  "<repo>/secrets/c2pa/sign.key"
set -euo pipefail

OUT="${1:-./secrets/c2pa}"
DAYS=3650
mkdir -p "$OUT"
cd "$OUT"

echo "[1/4] Root CA 키/인증서..."
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out ca.key
openssl req -new -x509 -key ca.key -out ca.crt -days "$DAYS" -sha256 \
  -subj "/CN=WeRU.B Root CA/O=WeRUB" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

echo "[2/4] 서명용(end-entity) 키/CSR..."
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out sign.key
openssl req -new -key sign.key -out sign.csr -subj "/CN=WeRU.B AI Signer/O=WeRUB"

echo "[3/4] CA로 서명용 인증서 발급 (EKU=emailProtection)..."
printf "keyUsage=critical,digitalSignature\nextendedKeyUsage=critical,emailProtection\n" > ext.cnf
openssl x509 -req -in sign.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out sign.crt -days "$DAYS" -sha256 -extfile ext.cnf

echo "[4/4] 체인 결합 (sign + ca)..."
cat sign.crt ca.crt > chain.pem
chmod 600 ca.key sign.key
rm -f sign.csr ext.cnf ca.srl

echo "완료. 생성 파일:"
ls -l "$PWD"/{sign.key,chain.pem}
echo
echo "config.json 에 추가:"
echo "  \"ai_marking_c2pa\": true,"
echo "  \"c2pa_cert_path\": \"$PWD/chain.pem\","
echo "  \"c2pa_key_path\": \"$PWD/sign.key\""
echo "⚠️ sign.key/ca.key 는 비밀키 — 절대 커밋 금지(.gitignore 처리됨)."
