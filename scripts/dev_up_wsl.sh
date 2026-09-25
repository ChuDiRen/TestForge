#!/usr/bin/env bash
# WSL 后端编排（单体）：一个进程 = gateway + 全部服务（MONO_MODE=1）
# 用法：wsl.exe -e bash scripts/dev_up_wsl.sh
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
RUN="$ROOT/.run"
LOGS="$RUN/logs"
mkdir -p "$LOGS"
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv-wsl"
export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-https://mirrors.aliyun.com/pypi/simple}"
export UV_HTTP_TIMEOUT=300
export MONO_MODE="${MONO_MODE:-1}"

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && { exec 3>&- 3<&-; return 0; } || return 1; }

echo "[wsl] uv sync (.venv-wsl)…"
uv sync --quiet 2>&1 | tail -2 || true
PY="$ROOT/.venv-wsl/bin/python"
[ -x "$PY" ] || { echo "[wsl] .venv-wsl 创建失败"; exit 1; }

echo "[wsl] 启动后端（单体：gateway + 9 服务进程内）…"
if port_open 8000; then echo "  = backend 已在 :8000，跳过"; else
  for attempt in 1 2 3; do
    (PYTHONPATH="$ROOT" nohup "$PY" -u -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000 >"$LOGS/gateway.log" 2>&1 & echo $! >"$RUN/gateway.pid")
    echo "  + backend pid=$(cat "$RUN/gateway.pid") -> :8000 (attempt $attempt)"
    ok=0
    dl=$((SECONDS + 30))
    while [ $SECONDS -lt $dl ]; do
      port_open 8000 && { ok=1; break; }
      sleep 0.4
    done
    [ $ok -eq 1 ] && break
    echo "  ! backend 第 $attempt 次未就绪，重试…"
    sleep 1
  done
fi

dl=$((SECONDS + 30))
while [ $SECONDS -lt $dl ]; do port_open 8000 && break; sleep 0.4; done
port_open 8000 && echo "[wsl] 后端就绪：http://127.0.0.1:8000（单体，Windows 经 localhost 转发访问）" || { echo "[wsl] backend 启动失败（$LOGS/gateway.log）"; exit 1; }
