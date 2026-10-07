"""round-04 终版验收：服务调用链路 + Sigma 图谱 + Wiki 体检/互链，亮暗两态。"""
import os
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(__file__)
BASE = "http://127.0.0.1:5173"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    page.goto(BASE, wait_until="networkidle")
    page.get_by_text("一键登录", exact=False).click()
    page.wait_for_timeout(2200)

    # 服务调用链路
    page.goto(f"{BASE}/?view=map", wait_until="networkidle")
    page.wait_for_timeout(1800)
    page.screenshot(path=os.path.join(OUT, "final-light-servicemap.png"))
    # Wiki（体检卡 + 抽屉互链）
    page.goto(f"{BASE}/?view=wiki", wait_until="networkidle")
    page.wait_for_timeout(2200)
    page.screenshot(path=os.path.join(OUT, "final-light-wiki.png"))
    # Sigma 图谱（等 FA2 收敛）
    page.goto(f"{BASE}/?view=graph", wait_until="networkidle")
    page.wait_for_timeout(16000)
    page.screenshot(path=os.path.join(OUT, "final-light-graph.png"))

    # 暗色三连
    page.get_by_role("button", name="切换到暗色主题").click()
    page.wait_for_timeout(1200)
    page.screenshot(path=os.path.join(OUT, "final-dark-graph.png"))
    page.goto(f"{BASE}/?view=map", wait_until="networkidle")
    page.wait_for_timeout(1500)
    page.screenshot(path=os.path.join(OUT, "final-dark-servicemap.png"))
    page.goto(f"{BASE}/?view=wiki", wait_until="networkidle")
    page.wait_for_timeout(2000)
    page.screenshot(path=os.path.join(OUT, "final-dark-wiki.png"))

    browser.close()
print("done")
