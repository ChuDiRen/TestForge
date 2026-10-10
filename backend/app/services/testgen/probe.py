"""探针式期望值捕获（特征化测试）：对被测真实函数实际执行，用真实输出反填断言。

数据真实性原则：期望值不来自人工模板，而来自对仓库检出的真实代码执行；
同一输入双跑结果不一致 → 判定非确定性，放弃该用例（诚实跳过，不造假）。
"""

from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path

from app.schemas.testgen import CasePlan

log = logging.getLogger("testgen.probe")

# 探针脚本与生成测试共用的归一化函数（dataclass→dict / set→有序 / tuple→list）
NORM_SRC = '''\
def _norm(v):
    import dataclasses
    if hasattr(v, "__dataclass_fields__"):
        return {k: _norm(x) for k, x in dataclasses.asdict(v).items()}
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(repr(_norm(x)) for x in v)
    return v
'''

PROBE_PREFIX = "PROBE"
# 固定时钟：时间依赖函数（token 过期/签名含时戳）的期望值必须与沙箱执行同钟才可重放。
# probe 与 codegen 渲染的测试统一使用此常量（见 codegen._FIXED_TS）。
FIXED_TS = 1700000000


def _probe_script(module: str, fn: str, cases: list[dict]) -> str:
    """探针脚本：解包 input、按 patches 打桩（与 codegen 渲染的 monkeypatch 语义一致）。

    固定时钟（FIXED_TS）：时间依赖函数（token 过期/签名含时戳）的期望值必须与
    沙箱执行同钟才可重放；codegen 渲染的测试带同值 _tf_fixed_clock fixture。
    """
    return (
        "import importlib, json, sys, time\n"
        "import unittest.mock as _mock\n"
        "sys.path.insert(0, '.')\n"
        "import os\n"
        "# 仓库重构后 Python 包在 backend/ 子目录下：两种布局都兼容\n"
        "if os.path.isdir('backend') and not os.path.exists(os.path.join('.', 'services')):\n"
        "    sys.path.insert(0, os.path.abspath('backend'))\n"
        f"time.time = lambda: {FIXED_TS}.0  # 固定时钟，期望值可重放\n"
        f"{NORM_SRC}\n"
        f"from {module} import {fn}\n"
        f"CASES = {cases!r}\n"
        "out = []\n"
        "for case in CASES:\n"
        "    pms = []\n"
        "    try:\n"
        "        for p in case.get('patches') or []:\n"
        "            mod = importlib.import_module(p['module'])\n"
        "            if p.get('kind') == 'raise':\n"
        "                exc_mod = importlib.import_module(p.get('exc_module') or p['module'])\n"
        "                exc = getattr(exc_mod, p['exc'])()\n"
        "                raiser = (lambda e: (lambda *a, **k: (_ for _ in ()).throw(e)))(exc)\n"
        "                pms.append(_mock.patch.object(mod, p['attr'], raiser))\n"
        "            else:\n"
        "                cur = getattr(mod, p['attr'])\n"
        "                new = (lambda v: (lambda *a, **k: v))(p.get('value')) if callable(cur) else p.get('value')\n"
        "                pms.append(_mock.patch.object(mod, p['attr'], new))\n"
        "        for pm in pms:\n"
        "            pm.start()\n"
        "        try:\n"
        f"            r = _norm({fn}(**case['input']))\n"
        "            out.append({'ok': True, 'repr': repr(r)})\n"
        "        finally:\n"
        "            for pm in pms:\n"
        "                pm.stop()\n"
        "    except Exception as exc:\n"
        "        out.append({'ok': False, 'err': type(exc).__name__})\n"
        f"print('{PROBE_PREFIX}' + json.dumps(out, ensure_ascii=False))\n"
    )


def _run_probe(module: str, fn: str, cases: list[dict], checkout: Path, timeout: float = 90.0) -> list[dict] | None:
    """在仓库检出处真实执行目标函数；返回每个用例的捕获结果（导入失败返回 None）。"""
    script = _probe_script(module, fn, cases)
    runs: list[list[dict]] = []
    for _ in range(2):  # 双跑：不一致 = 非确定性
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(checkout),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("probe %s.%s 执行失败: %s", module, fn, exc)
            return None
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(PROBE_PREFIX)), None)
        if line is None:
            log.warning("probe %s.%s 无结果输出（模块导入失败?）: %s", module, fn, (proc.stderr or "")[-200:])
            return None
        runs.append(json.loads(line[len(PROBE_PREFIX) :]))
    return runs[0] if runs[0] == runs[1] else None


def fill_probe_expectations(plan: CasePlan, repo_checkout: str | Path, overwrite: bool = False) -> tuple[CasePlan, str]:
    """对清单内用例执行真实函数，反填 expected_return / expected_error_type。

    默认只填尚无期望值的用例（人工精选清单不受影响）；overwrite=True 时对全部用例
    用真实执行结果覆写（用于 LLM/守卫的推测断言校正——现实即规格）。
    非确定性/不可序列化的用例被诚实剔除。
    """
    if not plan.cases:
        return plan, ""
    probe_cases = [
        c for c in plan.cases
        if overwrite
        or not (
            c.assert_return
            or c.expected_fields
            or c.expected_error
            or c.expected_error_type
            or c.expected_contains
            or c.expected_not_contains
        )
    ]
    if not probe_cases:
        return plan, ""

    module, fn = plan.module, plan.target.split(".")[-1]
    probe_payload = [
        {"input": c.input, "patches": [pa.model_dump() for pa in c.patches]}
        for c in probe_cases
    ]
    result = _run_probe(module, fn, probe_payload, Path(repo_checkout))
    if result is None:
        note = "探针未产出（非确定性或导入失败），保留清单不造假"
        log.info("probe %s.%s: %s", module, fn, note)
        return plan, note

    dropped: list[str] = []
    for case, r in zip(probe_cases, result):
        if r["ok"]:
            # 真实执行成功 → 异常断言（LLM 推测）必须清掉，否则 codegen 渲染成 raises 与现实矛盾
            case.expected_error = ""
            case.expected_error_type = ""
            try:
                case.expected_return = ast.literal_eval(r["repr"])
            except (ValueError, SyntaxError):
                dropped.append(case.id)
                continue
            case.assert_return = True
        else:
            case.expected_error = ""  # 业务错误码探针不可观测，以真实异常类名为准
            case.expected_return = None
            case.assert_return = False
            case.expected_error_type = r["err"]

    for cid in dropped:
        plan.cases = [c for c in plan.cases if c.id != cid]
    kept = len(probe_cases) - len(dropped)
    note = f"探针捕获 {kept}/{len(probe_cases)} 条期望值（双跑一致性校验通过）"
    log.info("probe %s.%s: %s", module, fn, note)
    return plan, note
