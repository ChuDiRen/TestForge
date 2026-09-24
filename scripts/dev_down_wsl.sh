#!/usr/bin/env bash
# WSL 后端停止：按 .run/*.pid 杀进程
cd "$(dirname "$0")/.."
RUN=".run"
if [ ! -d "$RUN" ]; then echo "无 .run"; exit 0; fi
n=0
for f in "$RUN"/*.pid; do
  [ -f "$f" ] || continue
  pid=$(cat "$f")
  case "$f" in *frontend*) continue;; esac  # frontend 属 Windows 侧
  kill "$pid" 2>/dev/null && n=$((n+1)) && echo "  - $(basename "$f" .pid) pid=$pid"
  rm -f "$f"
done
echo "[wsl-down] stopped $n"
