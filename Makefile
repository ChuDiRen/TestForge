# TestForge 根 Makefile（Windows Git Bash / Linux 通用）
# 仓库形态：frontend/（React 前端） + backend/（Python 后端工程根）
# 后端命令统一在 backend/ 下执行（uv 工程根 = backend/，.env/data/.run 也在 backend/ 下）

UV := uv
FRONTEND_PORT ?= 5173
APP_PORT ?= 8000

.PHONY: help install dev up down restart test lint fmt demo-m0 demo-m1 demo-m2 demo-m3 demo-m4 demo-m5 seed clean stack-up stack-down mcp analyze rag-eval kg-build kg-rebuild-vdb kg-clean-cache kg-repair kg-communities

help:
	@echo "make install   安装依赖（backend: uv sync + frontend: pnpm install）"
	@echo "make dev       本地一键拉起后端+前端（无 Docker）"
	@echo "make up        同 dev：优先 Docker，缺失时本地模式全绿"
	@echo "make down      停止本地全部服务"
	@echo "make test      pytest 单测（backend/）"
	@echo "make lint      ruff + mypy（backend/）"
	@echo "make demo-mX   里程碑验收脚本（m0~m5）"
	@echo "make stack-up  docker compose 全栈（需 docker）"
	@echo "make mcp       启动 MCP server（stdio）"
	@echo "make analyze|kg-build|rag-eval  知识增强工具"

install:
	cd backend && $(UV) sync
	pnpm --dir frontend install


dev:
	cd backend && $(UV) run python scripts/dev_up.py

up: dev
	@cd backend && $(UV) run python scripts/healthcheck.py

down:
	cd backend && $(UV) run python scripts/dev_down.py

restart: down dev

test:
	cd backend && $(UV) run pytest -q

lint:
	cd backend && $(UV) run ruff check gateway services scripts tests
	cd backend && $(UV) run mypy gateway services

fmt:
	cd backend && $(UV) run ruff check --fix gateway services scripts tests

seed:
	cd backend && $(UV) run python scripts/seed.py

reset-data:
	cd backend && $(UV) run python scripts/reset_db.py

seed-real:
	cd backend && $(UV) run python scripts/seed_real.py

llm-check:
	cd backend && $(UV) run python scripts/llm_check.py

# ---- 知识增强（GitNexus/LightRAG 借鉴改造）----

mcp:
	cd backend && $(UV) run python -m app.mcp_server

analyze:
	cd backend && $(UV) run python scripts/repo_analyze.py

rag-eval:
	cd backend && $(UV) run python scripts/eval_rag.py

kg-build:
	cd backend && $(UV) run python scripts/kg_build.py

kg-rebuild-vdb:
	cd backend && $(UV) run python scripts/kg_rebuild_vdb.py

kg-clean-cache:
	cd backend && $(UV) run python scripts/kg_clean_cache.py

kg-repair:
	cd backend && $(UV) run python scripts/kg_repair.py

kg-communities:
	cd backend && $(UV) run python -c "from app.db.session import init_db; init_db(); from app.services.knowledge.kg_communities import build_communities; print(build_communities())"

demo-m0:
	cd backend && $(UV) run python scripts/demo_m0.py

demo-m1:
	cd backend && $(UV) run python scripts/demo_m1.py

demo-m2:
	cd backend && $(UV) run python scripts/demo_m2.py

demo-m3:
	cd backend && $(UV) run python scripts/demo_m3.py

demo-m4:
	cd backend && $(UV) run python scripts/demo_m4.py

demo-m5:
	cd backend && $(UV) run python scripts/demo_m5.py

clean:
	-cd backend && $(UV) run python scripts/dev_down.py
	rm -rf backend/.run backend/data backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache .run

stack-up:
	cd deploy && docker compose up -d --build

stack-down:
	cd deploy && docker compose down
