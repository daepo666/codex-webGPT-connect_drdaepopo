#!/usr/bin/env bash
set -euo pipefail
python agent.py \
  --url "https://example.com" \
  --goal "이 페이지가 무엇인지 확인하고 목표를 완료했다고 판단하면 done을 선택해라." \
  --max-steps 3 \
  --headless
