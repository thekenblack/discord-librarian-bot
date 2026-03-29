#!/bin/bash
# gsync - 현재 브랜치를 remote와 동기화
branch=$(git branch --show-current)
echo "=== Fetching ==="
git fetch origin
echo ""
echo "=== Pulling $branch ==="
git pull origin "$branch" --rebase
echo ""
echo "=== Pushing $branch ==="
git push origin "$branch"
