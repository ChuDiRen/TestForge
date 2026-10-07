"""graph 页沉浸式改版验收截图。"""
import os
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(__file__)
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    page.goto("http://127.0.0.1:5173", wait_until="networkidle")
    page.get_by_text("一键登录", exact=False).click()
    page.wait_for_timeout(2200)
    page.goto("http://127.0.0.1:5173/?view=graph", wait_until="networkidle")
    page.wait_for_timeout(16000)
    page.screenshot(path=os.path.join(OUT, "v2-light-graph.png"))
    page.get_by_role("button", name="切换到暗色主题").click()
    page.wait_for_timeout(1500)
    page.screenshot(path=os.path.join(OUT, "v2-dark-graph.png"))
    browser.close()
print("done")
