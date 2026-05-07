const { chromium } = require('/Users/shable/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const BASE_URL = 'http://127.0.0.1:5001/';
const SOURCE_URL = 'http://127.0.0.1:5001/static/1.1-three-pages.md';

async function fetchJson(page, path, options = {}) {
  return page.evaluate(async ({ path, options }) => {
    const response = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });
    const text = await response.text();
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = text;
    }
    return { ok: response.ok, status: response.status, payload };
  }, { path, options });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const projectName = `1.1前三页browser精修测试-${String(Date.now()).slice(-6)}`;

  try {
    await page.goto(BASE_URL, { waitUntil: 'load', timeout: 15000 });
    let bodyText = await page.locator('body').innerText({ timeout: 10000 });
    if (bodyText.includes('未登录')) {
      await page.getByLabel('邮箱').fill('admin@163.com');
      await page.getByLabel('密码').fill('admin123');
      await page.getByRole('button', { name: '登录', exact: true }).click();
      await page.getByText('新建项目', { exact: false }).waitFor({ state: 'visible', timeout: 15000 });
    }

    await page.getByPlaceholder('例如：Q3经营复盘', { exact: true }).fill(projectName);
    await page.getByRole('button', { name: '创建', exact: true }).click();
    await page.getByText('待导入', { exact: false }).waitFor({ state: 'visible', timeout: 20000 });

    await page.getByPlaceholder('https://...', { exact: true }).fill(SOURCE_URL);
    await page.getByRole('button', { name: '导入', exact: true }).click();
    await page.getByText('源材料已就绪', { exact: true }).waitFor({ state: 'visible', timeout: 30000 });

    const projectsResult = await fetchJson(page, '/api/projects');
    if (!projectsResult.ok) {
      throw new Error(`list projects failed: ${projectsResult.status}`);
    }
    const project = projectsResult.payload.data.find((item) => item.name === projectName);
    if (!project) {
      throw new Error(`created project not found: ${projectName}`);
    }

    await page.getByRole('button', { name: '生成 PPT', exact: true }).click();
    await page.getByText('generate_ppt', { exact: false }).waitFor({ state: 'visible', timeout: 15000 });
    console.log(JSON.stringify({ event: 'started', projectName, projectId: project.id }));

    let lastLine = '';
    for (let i = 0; i < 100; i += 1) {
      const projectResult = await fetchJson(page, `/api/projects/${project.id}`);
      if (!projectResult.ok) {
        throw new Error(`get project failed: ${projectResult.status}`);
      }
      const latest = projectResult.payload.data.recentJobs[0];
      const files = projectResult.payload.data.files;
      const log = latest.log || '';
      const statusLine = JSON.stringify({
        event: 'poll',
        status: latest.status,
        stage: latest.stage,
        svg: files.svgOutput.length,
        final: files.svgFinal.length,
        exports: files.exports.length,
        refining: log.includes('[AI image prompt] refining'),
        promptModel: log.includes('[次]deepseek-v4-flash'),
      });
      if (statusLine !== lastLine) {
        console.log(statusLine);
        lastLine = statusLine;
      }
      if (!['running', 'queued'].includes(latest.status)) {
        console.log(JSON.stringify({ event: 'finished', projectName, projectId: project.id, job: latest }));
        break;
      }
      await page.waitForTimeout(30000);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
