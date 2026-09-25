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

from services.testgen_svc.schemas import CasePlan

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


def _probe_script(module: str, fn: str, inputs: list[dict]) -> str:
    return (
        "import json, sys\n"
        "sys.path.insert(0, '.')\n"
        f"{NORM_SRC}\n"
        f"from {module} import {fn}\n"
        f"CASES = {json.dumps(inputs, ensure_ascii=False)}\n"
        "out = []\n"
        "for kw in CASES:\n"
        "    try:\n"
        f"        r = _norm({fn}(**kw))\n"
        "        out.append({'ok': True, 'repr': repr(r)})\n"
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
        if overwrite or not (c.assert_return or c.expected_fields or c.expected_error or c.expected_error_type)
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
            try:
                case.expected_return = ast.literal_eval(r["repr"])
            except (ValueError, SyntaxError):
                dropped.append(case.id)
                continue
            case.assert_return = True
        else:
            case.expected_error_type = r["err"]

    for cid in dropped:
        plan.cases = [c for c in plan.cases if c.id != cid]
    kept = len(probe_cases) - len(dropped)
    note = f"探针捕获 {kept}/{len(probe_cases)} 条期望值（双跑一致性校验通过）"
    log.info("probe %s.%s: %s", module, fn, note)
    return plan, note
