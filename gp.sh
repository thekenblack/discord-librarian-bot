#!/bin/bash
# gp - 커밋 + 푸시 한방
# Usage: gp "커밋 메시지"
msg="${1:-sync}"
git add -A
git commit -m "$msg"
branch=$(git branch --show-current)
git push origin "$branch"
