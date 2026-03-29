#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "[오류] .env 파일이 없습니다. ./install.sh를 먼저 실행하세요."
    exit 1
fi

if [ ! -d venv ]; then
    echo "[오류] venv가 없습니다. ./install.sh를 먼저 실행하세요."
    exit 1
fi

exec venv/bin/python startup.py
