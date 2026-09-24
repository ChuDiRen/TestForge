#!/usr/bin/env bash
# WSL 后端编排：7 个 gRPC 服务 + gateway（Windows 侧 asyncio 被三方软件注入破坏时的运行方式）
# 用法：wsl.exe -e bash scripts/dev_up_wsl.sh
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
RUN="$ROOT/.run"
LOGS="$RUN/logs"
mkdir -p "$LOGS"
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv-wsl"
export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-https://mirrors.aliyun.com/pypi/simple}"
export UV_HTTP_TIMEOUT=300

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && { exec 3>&- 3<&-; return 0; } || return 1; }

echo "[wsl] uv sync (.venv-wsl)…"
uv sync --quiet 2>&1 | tail -2 || true
PY="$ROOT/.venv-wsl/bin/python"
[ -x "$PY" ] || { echo "[wsl] .venv-wsl 创建失败"; exit 1; }

start_svc() { # name module port
  local name="$1" mod="$2" port="$3"
  if port_open "$port"; then echo "  = $name 已在 :$port，跳过"; return 0; fi
  (PYTHONPATH="$ROOT" nohup "$PY" -u -m "$mod" >"$LOGS/$name.log" 2>&1 & echo $! >"$RUN/$name.pid")
  echo "  + $name pid=$(cat "$RUN/$name.pid") -> :$port"
}

wait_port() { # port label timeout_s
  local dl=$((SECONDS + $3))
  while [ $SECONDS -lt $dl ]; do port_open "$1" && return 0; sleep 0.4; done
  echo "  ! $2 :$1 超时未就绪（$LOGS）"; return 1
}

echo "[wsl] 启动 gRPC 服务…"
start_svc trace-svc        services.trace_svc.main        50057
start_svc repo-svc         services.repo_svc.main         50051
start_svc wiki-builder     services.wiki_builder.main     50052
start_svc contract-registry services.contract_registry.main 50053
start_svc req-svc          services.req_svc.main          50054
start_svc testgen-svc      services.testgen_svc.main      50055
start_svc runner-svc       services.runner_svc.main       50056
for p in 50057 50051 50052 50053 50054 50055 50056; do wait_port $p svc 25; done

echo "[wsl] 启动 gateway…"
if port_open 8000; then echo "  = gateway 已在 :8000，跳过"; else
  (PYTHONPATH="$ROOT" nohup "$PY" -u -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000 >"$LOGS/gateway.log" 2>&1 & echo $! >"$RUN/gateway.pid")
  echo "  + gateway pid=$(cat "$RUN/gateway.pid") -> :8000"
fi
wait_port 8000 gateway 30

echo "[wsl] 后端就绪：gateway http://127.0.0.1:8000（Windows 经 localhost 转发访问）"
