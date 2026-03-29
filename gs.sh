#!/bin/bash
# gs - git status 요약 + 최근 커밋 3개
echo "=== Branch ==="
git branch --show-current
echo ""
echo "=== Status ==="
git status -s
echo ""
echo "=== Recent Commits ==="
git log --oneline -5 --decorate
