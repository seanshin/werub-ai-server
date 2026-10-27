#!/usr/bin/env bash
# 저녁 재부팅 예약 — v2.125.68부터 scripts/maintenance_window.sh의 얇은 래퍼(이름·사용법 호환용).
#   sudo scripts/schedule_evening_reboot.sh [YYYY-MM-DD HH:MM]   (기본: 오늘 22:00)
# = maintenance_window.sh --schedule … --mode reboot-only
# 종전 동작(journal vacuum → 재부팅)에 **재부팅 전 관문**(dkms·initrd·블랙리스트·디스크)과 기준값 기록이 더해졌다.
# 업그레이드까지 하려면: sudo scripts/maintenance_window.sh --schedule "YYYY-MM-DD HH:MM" --mode full
set -eu
WHEN="${1:-$(date +%F) 22:00}"
exec "$(dirname "$0")/maintenance_window.sh" --schedule "$WHEN" --mode reboot-only
