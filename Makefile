# TestForge 根 Makefile（Windows Git Bash / Linux 通用）
# 目标：install / proto / dev / up / down / test / lint / demo-mX / stack-up（compose）

UV := uv
PY := $(UV) run python
FRONTEND_PORT ?= 5173
GATEWAY_PORT ?= 8000

.PHONY: help install proto dev up down restart test lint fmt demo-m0 demo-m1 demo-m2 demo-m3 demo-m4 demo-m5 seed clean stack-up stack-down

help:
	@echo "make install   安装依赖（uv sync + pnpm install）"
	@echo "make proto     生成 gRPC stub（proto/ → services/shared/gen）"
	@echo "make dev       本地一键拉起全部服务+前端（无 Docker）"
	@echo "make up        同 dev：优先 Docker，缺失时本地模式全绿"
	@echo "make down      停止本地全部服务"
	@echo "make test      pytest 单测"
	@echo "make lint      ruff + mypy"
	@echo "make demo-mX   里程碑验收脚本（m0~m5）"
	@echo "make stack-up  docker compose 全栈（需 docker）"

install:
	$(UV) sync
	pnpm --dir frontend install

proto:
	$(PY) -m grpc_tools.protoc -Iproto --python_out=services/shared/gen --grpc_python_out=services/shared/gen proto/testforge.proto
	$(PY) scripts/fix_proto_imports.py
	@echo "proto stubs generated -> services/shared/gen/"

dev:
	$(PY) scripts/dev_up.py
# Windows 主机 asyncio 被三方注入破坏时的备选（WSL 后端 + Windows 前端）：
# 	$(PY) scripts/dev_up_win.py

up: dev
	@$(PY) scripts/healthcheck.py

down:
	$(PY) scripts/dev_down.py
	-wsl.exe -e bash scripts/dev_down_wsl.sh

restart: down dev

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check gateway services scripts tests
	$(UV) run mypy gateway services

fmt:
	$(UV) run ruff check --fix gateway services scripts tests

seed:
	$(PY) scripts/seed.py

reset-data:
	$(PY) scripts/reset_db.py

seed-real:
	$(PY) scripts/seed_real.py

demo-m0:
	$(PY) scripts/demo_m0.py

demo-m1:
	$(PY) scripts/demo_m1.py

demo-m2:
	$(PY) scripts/demo_m2.py

demo-m3:
	$(PY) scripts/demo_m3.py

demo-m4:
	$(PY) scripts/demo_m4.py

demo-m5:
	$(PY) scripts/demo_m5.py

clean:
	-$(PY) scripts/dev_down.py
	rm -rf .run data .pytest_cache .ruff_cache .mypy_cache

stack-up:
	cd deploy && docker compose up -d --build

stack-down:
	cd deploy && docker compose down
