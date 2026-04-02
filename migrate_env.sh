#!/bin/bash
# env.example 기반으로 .env를 재생성
# 기존 .env에서 매칭되는 값을 가져오고, 없으면 example 기본값 유지

OLD_ENV=".env"
EXAMPLE="env.example"
NEW_ENV=".env.new"

if [ ! -f "$EXAMPLE" ]; then
    echo "env.example 파일이 없습니다."
    exit 1
fi

if [ ! -f "$OLD_ENV" ]; then
    echo "기존 .env 파일이 없습니다. env.example을 복사합니다."
    cp "$EXAMPLE" "$OLD_ENV"
    exit 0
fi

# 기존 .env에서 KEY=VALUE 읽기
declare -A old_values
while IFS= read -r line; do
    # 빈 줄, 주석 건너뜀
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    old_values["$key"]="$value"
done < "$OLD_ENV"

# env.example 기반으로 새 .env 생성
while IFS= read -r line; do
    # 주석이나 빈 줄은 그대로
    if [[ -z "$line" || "$line" =~ ^# ]]; then
        echo "$line" >> "$NEW_ENV"
        continue
    fi

    key="${line%%=*}"
    if [[ -n "${old_values[$key]+x}" ]]; then
        # 기존 값 있으면 가져옴
        echo "${key}=${old_values[$key]}" >> "$NEW_ENV"
    else
        # 없으면 example 기본값
        echo "$line" >> "$NEW_ENV"
    fi
done < "$EXAMPLE"

# 백업 후 교체
cp "$OLD_ENV" ".env.bak"
mv "$NEW_ENV" "$OLD_ENV"
echo ".env 마이그레이션 완료 (백업: .env.bak)"
