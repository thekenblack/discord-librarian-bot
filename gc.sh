#!/bin/bash
# gc - 변경된 파일 전부 스테이징 + 커밋
# Usage: gc "커밋 메시지"
msg="${1:-sync}"
git add -A
git commit -m "$msg"
