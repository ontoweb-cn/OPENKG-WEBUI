/**
 * P0 一次性 live 验证脚本（非回归套件）：知识库详情页标签页 + 深链 + 预览抽屉。
 * 用法：node scripts/p0-live-check.mjs（需 WEB_BASE_URL，默认 localhost:3001）
 */
import { chromium } from "playwright";

const BASE = process.env.WEB_BASE_URL || "http://localhost:3001";
const DATASET = process.env.KC_DATASET_ID || "73df73daa27311f190fc9b900814cd08";

const browser = await chromium.launch({
  executablePath:
    process.env.PW_CHROMIUM ||
    `${process.env.HOME}/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`,
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 900 },
  locale: "en-US",
});
const results = [];
const check = (name, ok, extra = "") => {
  results.push(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? ` — ${extra}` : ""}`);
};

try {
  await page.goto(`${BASE}/knowledge-center/${DATASET}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  check("page loads", true, page.url());

  // 登录墙检测
  const onLogin = /login|signin/i.test(page.url());
  check("no login wall", !onLogin, page.url());

  // T1：四个 tab
  const tabs = page.getByRole("tab");
  const tabCount = await tabs.count();
  check("4 tabs render", tabCount === 4, `count=${tabCount}`);

  // T3：拖放区
  const dropzone = page.getByText("拖放文件到此处，或点击选择文件。");
  check("dropzone visible on documents tab", await dropzone.first().isVisible().catch(() => false));

  // T1：切换 Sources / Retrieval / Settings
  for (const label of ["来源", "检索", "设置"]) {
    await page.getByRole("tab", { name: label }).click();
    await page.waitForTimeout(400);
  }
  check(
    "sources panel reachable",
    await page.getByText("GitHub", { exact: false }).first().isVisible().catch(() => false),
  );

  // T4：设置面板默认知识库
  await page.getByRole("tab", { name: "设置" }).click();
  await page.waitForTimeout(600);
  check(
    "default-kb toggle visible",
    await page
      .getByText("设为我的默认知识库", { exact: false })
      .isVisible()
      .catch(() => false),
  );
  await page.screenshot({ path: "/tmp/p0-settings.png", fullPage: false });

  // T1：深链直达
  await page.goto(`${BASE}/knowledge-center/${DATASET}?section=retrieval`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  check(
    "deep link ?section=retrieval",
    await page.getByText("检索试玩", { exact: false }).isVisible().catch(() => false),
  );

  // T2：文档名点击 → 预览抽屉
  await page.goto(`${BASE}/knowledge-center/${DATASET}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  const nameButton = page.locator("table tbody tr").first().locator("button").first();
  const hasDocs = (await page.locator("table tbody tr").count()) > 0;
  if (hasDocs) {
    await nameButton.click();
    await page.waitForTimeout(2500);
    const drawer = page.locator('[role="dialog"]');
    check("preview drawer opens", (await drawer.count()) > 0);
    await page.screenshot({ path: "/tmp/p0-preview.png", fullPage: false });
  } else {
    check("preview drawer opens", false, "no documents in dataset");
  }
} catch (err) {
  check("script completed", false, String(err).slice(0, 200));
} finally {
  await browser.close();
  console.log(results.join("\n"));
  const failed = results.filter((line) => line.startsWith("FAIL")).length;
  process.exit(failed > 0 ? 1 : 0);
}
