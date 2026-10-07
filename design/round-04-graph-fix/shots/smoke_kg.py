"""Sigma 知识图谱冒烟：控制台错误收集 + 点节点 + 影响半径 + 亮暗截图。"""
import os
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(__file__)
BASE = "http://127.0.0.1:5173"
errors = []

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE, wait_until="networkidle")
    page.get_by_text("一键登录", exact=False).click()
    page.wait_for_timeout(2000)
    page.goto(f"{BASE}/?view=graph", wait_until="networkidle")
    page.wait_for_timeout(16000)  # 等 FA2 收敛（14s 档）

    page.screenshot(path=os.path.join(OUT, "sigma-light.png"))

    # 点一个函数节点（canvas 内部点击取画布中心偏移试试 hover 后点击）
    canvas = page.locator("canvas").last
    box = canvas.bounding_box()
    if box:
        # hover 几个位置再点，触发 enterNode/clickNode 链路
        for dx, dy in [(0.4, 0.45), (0.55, 0.55), (0.5, 0.5)]:
            page.mouse.move(box["x"] + box["width"] * dx, box["y"] + box["height"] * dy)
            page.wait_for_timeout(300)
        page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(OUT, "sigma-light-selected.png"))

    # 暗色
    page.get_by_role("button", name="切换到暗色主题").click()
    page.wait_for_timeout(1500)
    page.screenshot(path=os.path.join(OUT, "sigma-dark.png"))

    browser.close()

print("console errors:", len(errors))
for e in errors[:10]:
    print(" -", e[:200])
