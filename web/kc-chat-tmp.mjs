import { chromium } from "playwright-core";
const browser = await chromium.launch({ headless: true, executablePath: "/home/simon/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.goto("http://localhost:3300/chat", { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForTimeout(8000);

// 作文输入：textarea 或 contenteditable
const ta = page.locator("textarea").first();
const editable = page.locator("[contenteditable='true']").first();
const target = (await ta.count()) > 0 ? ta : editable;
await target.click();
await target.fill("根据知识库内容，傅里叶变换是什么？");
await page.screenshot({ path: "/tmp/kc-chat-1.png" });
await target.press("Enter");
console.log("sent");

// 等待回答出现（LLM + 检索，放宽到 120s），记录回答文本片段
let answer = "";
for (let i = 0; i < 24; i += 1) {
  await page.waitForTimeout(5000);
  answer = await page.evaluate(() => document.body.innerText.slice(0, 4000));
  if (/傅里叶|Fourier|正弦/.test(answer) && answer.length > 600) break;
}
await page.screenshot({ path: "/tmp/kc-chat-2.png", fullPage: false });
const hit = /正弦分量|信号处理|Fourier transform 是信号处理/.test(answer);
console.log("answer-mentions-kb-content:", hit);
await browser.close();
process.exit(hit ? 0 : 1);
