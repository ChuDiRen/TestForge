"""round-04 服务地图/知识图谱重设计验收截图：亮暗两态各拍两张。"""
import os
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(__file__)
BASE = "http://127.0.0.1:5173"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    page.goto(BASE, wait_until="networkidle")
    # 一键登录（演示管理员）
    page.get_by_text("一键登录", exact=False).click()
    page.wait_for_timeout(2500)

    for view, name in [("map", "servicemap"), ("graph", "knowledgegraph")]:
        page.goto(f"{BASE}/?view={view}", wait_until="networkidle")
        page.wait_for_timeout(4500 if view == "graph" else 1500)
        page.screenshot(path=os.path.join(OUT, f"light-{name}.png"))

        # 切暗色：顶栏月亮按钮
        page.get_by_role("button", name="切换到暗色主题").click()
        page.wait_for_timeout(4500 if view == "graph" else 1200)
        page.screenshot(path=os.path.join(OUT, f"dark-{name}.png"))
        page.get_by_role("button", name="切换到亮色主题").click()
        page.wait_for_timeout(800)

    browser.close()
print("done:", sorted(f for f in os.listdir(OUT) if f.endswith(".png")))
