#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "[설치] venv 생성 중..."
python3 -m venv venv

echo "[설치] 패키지 설치 중..."
venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
    cp env.example .env
    echo "[설치] .env 파일이 생성되었습니다. 토큰과 API 키를 입력하세요."
    echo "  → nano .env"
else
    echo "[설치] .env 파일이 이미 존재합니다."
fi

echo "[설치] 완료! 실행: ./run.sh"
