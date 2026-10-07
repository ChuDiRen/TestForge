"""round-05 验收：仓库命名修复 + 循环依赖 chip + Wiki 问答侧栏。"""
import os
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(__file__)
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    page.goto("http://127.0.0.1:5173", wait_until="networkidle")
    page.get_by_text("一键登录", exact=False).click()
    page.wait_for_timeout(2200)

    # 图谱页：仓库下拉应显示 TestForge（无 #1 无 .git），右上应有循环依赖 chip
    page.goto("http://127.0.0.1:5173/?view=graph", wait_until="networkidle")
    page.wait_for_timeout(16000)
    page.screenshot(path=os.path.join(OUT, "v3-light-graph.png"))

    # Wiki 问答：打开侧栏，真实提问等回答
    page.goto("http://127.0.0.1:5173/?view=wiki", wait_until="networkidle")
    page.wait_for_timeout(2000)
    page.get_by_role("button", name="Wiki 问答").click()
    page.wait_for_timeout(600)
    page.locator("textarea").last.fill("订单创建的流程是怎样的？涉及哪些函数？")
    page.get_by_role("button", name="发送").click()
    page.wait_for_timeout(45000)
    page.screenshot(path=os.path.join(OUT, "v3-wiki-ask.png"))
    browser.close()
print("done")
