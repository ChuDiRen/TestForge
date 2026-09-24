#!/usr/bin/env bash
# 干净重启 WSL 后端：全杀 → 等端口释放 → 全启 → 自检
cd "$(dirname "$0")/.." || exit 1

pkill -f 'services\.[a-z_]+\.main' 2>/dev/null
pkill -f 'uvicorn gateway' 2>/dev/null
for i in $(seq 1 30); do
  ss -tln 2>/dev/null | grep -qE ':(8000|5005[1-7]) ' || break
  sleep 0.5
done
LEFT=$(ss -tln 2>/dev/null | grep -cE ':(8000|5005[1-7]) ' || true)
if [ "${LEFT:-0}" -gt 0 ]; then
  echo "[restart] 端口未释放，强制 -9"
  pkill -9 -f 'services\.[a-z_]+\.main' 2>/dev/null
  pkill -9 -f 'uvicorn gateway' 2>/dev/null
  sleep 1
fi

bash scripts/dev_up_wsl.sh
RC=$?
echo "[restart] dev_up rc=$RC"
curl -s --max-time 8 http://127.0.0.1:8000/api/system/services | head -c 200
echo
exit $RC
