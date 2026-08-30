#!/usr/bin/env node
/*
 * Upload downloaded Hermes Source Packs into NotebookLM from this Mac.
 *
 * This runs locally because NotebookLM needs a browser session that can access
 * Google. The script uses Playwright when available, opens Chrome with a
 * dedicated Hermes profile, and imports each queued .txt Source Pack as a
 * NotebookLM "Copied text" source.
 */

const childProcess = require("child_process");
const fs = require("fs");
const http = require("http");
const Module = require("module");
const os = require("os");
const path = require("path");
const readline = require("readline");

const DEFAULT_NOTEBOOKLM_URL = "https://notebooklm.google.com/";
const DEFAULT_CACHE_DIR = path.join(os.homedir(), "Documents", "Hermes NotebookLM Source Packs");
const DEFAULT_PROFILE_DIR = path.join(os.homedir(), "Library", "Application Support", "Hermes", "NotebookLM Chrome Profile");

function usage() {
  return `
Usage:
  node knowledge-base-stack/scripts/notebooklm_local_upload.js [options]

Options:
  --cache-dir <path>         Local Source Pack cache directory.
  --chrome-port <port>      Chrome remote debugging port. Default: 9222.
  --chrome-profile <path>   Dedicated Chrome profile directory.
  --method <method>         copied-text or file. Default: copied-text.
  --limit <n>               Max queue items to process. Default: 5.
  --job-key <key>           Upload only one job.
  --force                   Re-upload items even if status is uploaded.
  --dry-run                 Show what would be uploaded.
  --setup-login             Open NotebookLM and wait for you to finish Google login.
  --headless                Reserved for CI; not recommended for NotebookLM login.

Environment:
  NOTEBOOKLM_LOCAL_CACHE_DIR
  NOTEBOOKLM_CHROME_PROFILE_DIR
  NOTEBOOKLM_CHROME_PORT
  NOTEBOOKLM_DEFAULT_NOTEBOOK_URL
  NODE_PATH                 Optional; script also probes Codex bundled node_modules.
`.trim();
}

function parseArgs(argv) {
  const args = {
    cacheDir: process.env.NOTEBOOKLM_LOCAL_CACHE_DIR || DEFAULT_CACHE_DIR,
    chromePort: Number(process.env.NOTEBOOKLM_CHROME_PORT || 9222),
    chromeProfile: process.env.NOTEBOOKLM_CHROME_PROFILE_DIR || DEFAULT_PROFILE_DIR,
    method: "copied-text",
    limit: 5,
    jobKey: "",
    force: false,
    dryRun: false,
    setupLogin: false,
    headless: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`Missing value after ${arg}`);
      return argv[i];
    };
    if (arg === "--help" || arg === "-h") {
      console.log(usage());
      process.exit(0);
    } else if (arg === "--cache-dir") {
      args.cacheDir = next();
    } else if (arg === "--chrome-port") {
      args.chromePort = Number(next());
    } else if (arg === "--chrome-profile") {
      args.chromeProfile = next();
    } else if (arg === "--method") {
      args.method = next();
    } else if (arg === "--limit") {
      args.limit = Number(next());
    } else if (arg === "--job-key") {
      args.jobKey = next();
    } else if (arg === "--force") {
      args.force = true;
    } else if (arg === "--dry-run") {
      args.dryRun = true;
    } else if (arg === "--setup-login") {
      args.setupLogin = true;
    } else if (arg === "--headless") {
      args.headless = true;
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }
  if (!["copied-text", "file"].includes(args.method)) {
    throw new Error("--method must be copied-text or file");
  }
  if (!Number.isFinite(args.chromePort) || args.chromePort <= 0) {
    throw new Error("--chrome-port must be a positive number");
  }
  if (!Number.isFinite(args.limit) || args.limit <= 0) {
    throw new Error("--limit must be a positive number");
  }
  return args;
}

function expandHome(value) {
  if (!value || value === "~") return os.homedir();
  if (value.startsWith("~/")) return path.join(os.homedir(), value.slice(2));
  return value;
}

function readJson(file, fallback) {
  if (!fs.existsSync(file)) return fallback;
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function addNodeModuleSearchPaths() {
  const candidates = [
    process.env.NODE_PATH,
    path.join(process.cwd(), "node_modules"),
    path.join(__dirname, "..", "..", "node_modules"),
    path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules"),
  ]
    .filter(Boolean)
    .flatMap((value) => String(value).split(path.delimiter))
    .map(expandHome)
    .filter((value) => fs.existsSync(value));

  const current = (process.env.NODE_PATH || "").split(path.delimiter).filter(Boolean);
  for (const candidate of candidates) {
    if (!current.includes(candidate)) current.push(candidate);
  }
  process.env.NODE_PATH = current.join(path.delimiter);
  Module._initPaths();
}

function loadPlaywright() {
  addNodeModuleSearchPaths();
  try {
    return require("playwright");
  } catch (error) {
    throw new Error(
      [
        "Playwright is required for NotebookLM browser upload.",
        "Install it with one of:",
        "  npm install playwright",
        "  export NODE_PATH=\"$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules\"",
        `Original error: ${error.message}`,
      ].join("\n"),
    );
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function cdpJson(port, route = "/json/version", timeoutMs = 1000) {
  return new Promise((resolve, reject) => {
    const request = http.get(
      {
        host: "127.0.0.1",
        port,
        path: route,
        timeout: timeoutMs,
      },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if (response.statusCode && response.statusCode >= 400) {
            reject(new Error(`Chrome CDP returned ${response.statusCode}`));
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(error);
          }
        });
      },
    );
    request.on("timeout", () => {
      request.destroy(new Error("Chrome CDP timeout"));
    });
    request.on("error", reject);
  });
}

async function waitForChrome(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await cdpJson(port);
      return true;
    } catch {
      await sleep(500);
    }
  }
  return false;
}

async function ensureChrome(args) {
  if (await waitForChrome(args.chromePort, 1000)) return;
  if (process.platform !== "darwin") {
    throw new Error("Automatic Chrome launch is currently implemented for macOS only.");
  }
  fs.mkdirSync(args.chromeProfile, { recursive: true });
  childProcess
    .spawn(
      "open",
      [
        "-na",
        "Google Chrome",
        "--args",
        `--remote-debugging-port=${args.chromePort}`,
        `--user-data-dir=${args.chromeProfile}`,
        "--no-first-run",
        "--no-default-browser-check",
        DEFAULT_NOTEBOOKLM_URL,
      ],
      { detached: true, stdio: "ignore" },
    )
    .unref();
  if (!(await waitForChrome(args.chromePort, 20000))) {
    throw new Error(`Chrome did not expose remote debugging on port ${args.chromePort}.`);
  }
}

async function openNotebookBrowser(args, chromium) {
  if (await waitForChrome(args.chromePort, 500)) {
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${args.chromePort}`);
    const context = browser.contexts()[0] || (await browser.newContext());
    const page =
      context.pages().find((candidate) => candidate.url().includes("notebooklm.google.com")) ||
      (await context.newPage());
    return { context, page, close: async () => undefined };
  }

  fs.mkdirSync(args.chromeProfile, { recursive: true });
  const context = await chromium.launchPersistentContext(args.chromeProfile, {
    channel: "chrome",
    headless: args.headless,
    viewport: null,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page =
    context.pages().find((candidate) => candidate.url().includes("notebooklm.google.com")) ||
    context.pages()[0] ||
    (await context.newPage());
  return { context, page, close: async () => context.close() };
}

async function promptEnter(message) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  await new Promise((resolve) => rl.question(message, resolve));
  rl.close();
}

async function clickFirst(locators, label, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    for (const locator of locators) {
      try {
        if ((await locator.count()) > 0) {
          const first = locator.first();
          if (await first.isVisible().catch(() => true)) {
            await first.click({ timeout: 2000 });
            return;
          }
        }
      } catch (error) {
        lastError = error;
      }
    }
    await sleep(300);
  }
  throw new Error(`Could not click ${label}${lastError ? `: ${lastError.message}` : ""}`);
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function notebookScopes(page) {
  const pages = page.context().pages();
  return pages.flatMap((candidate) => [candidate, ...candidate.frames()]);
}

async function clickFirstAcrossScopes(page, locatorFactories, label, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    for (const scope of notebookScopes(page)) {
      for (const factory of locatorFactories) {
        try {
          const locator = factory(scope);
          if ((await locator.count().catch(() => 0)) > 0) {
            const first = locator.first();
            if (await first.isVisible().catch(() => true)) {
              await first.click({ timeout: 3000 });
              return;
            }
          }
        } catch (error) {
          lastError = error;
        }
      }
    }
    await sleep(300);
  }
  throw new Error(`Could not click ${label}${lastError ? `: ${lastError.message}` : ""}`);
}

async function fillFirstAcrossScopes(page, locatorFactories, value, label, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    for (const scope of notebookScopes(page)) {
      for (const factory of locatorFactories) {
        try {
          const locator = factory(scope);
          if ((await locator.count().catch(() => 0)) > 0) {
            const first = locator.first();
            if (await first.isVisible().catch(() => true)) {
              await first.fill(value, { timeout: 5000 });
              await first.press("Enter").catch(() => {});
              return;
            }
          }
        } catch (error) {
          lastError = error;
        }
      }
    }
    await sleep(300);
  }
  throw new Error(`Could not fill ${label}${lastError ? `: ${lastError.message}` : ""}`);
}

async function getDialogOrPage(page) {
  const dialog = page.getByRole("dialog").last();
  if ((await dialog.count().catch(() => 0)) > 0 && (await dialog.isVisible().catch(() => false))) {
    return dialog;
  }
  return page;
}

async function detectLoginRequired(page) {
  const url = page.url();
  const body = await page.locator("body").innerText({ timeout: 5000 }).catch(() => "");
  return /accounts\.google\.com|signin|ServiceLogin/i.test(url) || /登录|Sign in|Use your Google Account/i.test(body);
}

async function ensureNotebook(page, item, args) {
  const configuredDefault = process.env.NOTEBOOKLM_DEFAULT_NOTEBOOK_URL || "";
  const notebookUrl = item.notebooklm_url || configuredDefault || "";
  if (/^https:\/\/notebooklm\.google\.com\/notebook\//.test(notebookUrl)) {
    await page.goto(notebookUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
    return;
  }

  await page.goto(DEFAULT_NOTEBOOKLM_URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
  if (await detectLoginRequired(page)) {
    throw new Error("Google login is required. Run with --setup-login first, then retry.");
  }
  await clickFirst(
    [
      page.getByRole("button", { name: /创建笔记本|Create notebook|Create new/i }),
      page.getByText(/创建笔记本|Create notebook|Create new/i),
    ],
    "Create notebook",
    20000,
  );
  await page.waitForLoadState("domcontentloaded", { timeout: 30000 }).catch(() => {});
  await sleep(3000);
}

async function openAddSourceDialog(page) {
  let scope = await getDialogOrPage(page);
  if (scope !== page && (await scope.getByText(/复制的文字|Copied text/i).count().catch(() => 0)) > 0) {
    return scope;
  }
  await clickFirst(
    [
      page.getByRole("button", { name: /添加来源|Add source|Add sources/i }),
      page.getByText(/添加来源|Add source|Add sources/i),
    ],
    "Add source",
    20000,
  );
  await sleep(1000);
  scope = await getDialogOrPage(page);
  return scope;
}

async function waitForEnabled(locator, label, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if ((await locator.count().catch(() => 0)) > 0 && (await locator.first().isEnabled().catch(() => false))) {
      return locator.first();
    }
    await sleep(300);
  }
  throw new Error(`${label} did not become enabled`);
}

async function findTextbox(scope) {
  const labelCandidates = [
    scope.getByLabel(/粘贴的文字|Paste copied text|Paste text/i),
    scope.getByRole("textbox"),
    scope.locator("textarea"),
    scope.locator("[contenteditable=true]"),
  ];
  for (const locator of labelCandidates) {
    if ((await locator.count().catch(() => 0)) > 0) return locator.last();
  }
  throw new Error("Could not find NotebookLM copied-text textbox");
}

async function addCopiedTextSource(page, item, text) {
  const scope = await openAddSourceDialog(page);
  await clickFirst(
    [
      scope.getByRole("button", { name: /复制的文字|Copied text/i }),
      scope.getByText(/复制的文字|Copied text/i),
    ],
    "Copied text",
    10000,
  );
  await sleep(800);

  const dialog = await getDialogOrPage(page);
  const textbox = await findTextbox(dialog);
  await textbox.fill(text, { timeout: 60000 });
  const insertButton = dialog.getByRole("button", { name: /插入|Insert/i });
  const enabledInsert = await waitForEnabled(insertButton, "Insert button", 20000);
  await enabledInsert.click({ timeout: 15000 });
  await sleep(9000);
  return {
    source_title: "粘贴的文字",
    upload_method: "copied_text",
  };
}

async function addFileSource(page, item, filePath) {
  const scope = await openAddSourceDialog(page);
  const fileChooserPromise = page.waitForEvent("filechooser", { timeout: 10000 });
  await clickFirst(
    [
      scope.getByRole("button", { name: /上传文件|Upload files?|Upload/i }),
      scope.getByText(/上传文件|Upload files?|Upload/i),
    ],
    "Upload file",
    10000,
  );
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles(filePath);
  await sleep(12000);
  return {
    source_title: path.basename(filePath),
    upload_method: "file",
  };
}

function driveUrlForItem(item) {
  return item.drive_url || (item.drive || {}).url || "";
}

function driveFileIdForItem(item) {
  if (item.drive_file_id) return item.drive_file_id;
  if ((item.drive || {}).file_id) return item.drive.file_id;
  const url = driveUrlForItem(item);
  const match = url.match(/\/d\/([^/]+)/) || url.match(/[?&]id=([^&]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function driveNameForItem(item) {
  return item.drive_name || (item.drive || {}).name || `NotebookLM Source Pack - ${item.title || item.job_key || "Research Pack"}`;
}

async function addGoogleDriveSource(page, item) {
  const driveName = driveNameForItem(item);
  const driveFileId = driveFileIdForItem(item);
  const searchTerms = [driveName, item.title, driveFileId].filter(Boolean);
  if (!driveUrlForItem(item) && !driveFileId) {
    throw new Error(`Google Drive source is missing for ${item.job_key || item.title}`);
  }

  await openAddSourceDialog(page);
  await clickFirstAcrossScopes(
    page,
    [
      (scope) => scope.getByRole("button", { name: /Google Drive|云端硬盘|Google 云端硬盘|Drive/i }),
      (scope) => scope.getByText(/Google Drive|云端硬盘|Google 云端硬盘/i),
    ],
    "Google Drive source",
    15000,
  );
  await sleep(3000);

  for (const term of searchTerms) {
    try {
      await fillFirstAcrossScopes(
        page,
        [
          (scope) => scope.getByRole("textbox", { name: /Search|搜索|查找/i }),
          (scope) => scope.getByPlaceholder(/Search|搜索|查找/i),
          (scope) => scope.locator("input[type='text']"),
        ],
        term,
        "Google Drive search box",
        12000,
      );
      await sleep(3000);
      await clickFirstAcrossScopes(
        page,
        [
          (scope) => scope.getByText(driveName, { exact: false }),
          (scope) => (item.title ? scope.getByText(item.title, { exact: false }) : scope.locator("__missing__")),
          (scope) => scope.getByText(new RegExp(escapeRegExp(term), "i")),
        ],
        `Google Drive file ${driveName}`,
        12000,
      );
      break;
    } catch (error) {
      if (term === searchTerms[searchTerms.length - 1]) throw error;
    }
  }

  await clickFirstAcrossScopes(
    page,
    [
      (scope) => scope.getByRole("button", { name: /插入|Insert|Select|选择|Add/i }),
      (scope) => scope.getByText(/插入|Insert|Select|选择|Add/i),
    ],
    "Insert Google Drive source",
    15000,
  );
  await sleep(12000);
  return {
    source_title: driveName,
    upload_method: "google_drive",
    drive_file_id: driveFileId,
    drive_url: driveUrlForItem(item),
  };
}

function queueItems(queue, args) {
  const items = Array.isArray(queue.items) ? queue.items : [];
  return items
    .filter((item) => !args.jobKey || item.job_key === args.jobKey)
    .filter((item) => {
      if (args.force) return true;
      if (["uploaded", "skipped"].includes(item.status)) return false;
      if (item.status === "drive_synced") return false;
      return true;
    })
    .slice(0, args.limit);
}

async function uploadItem(page, item, args) {
  const downloads = item.downloads || {};
  const textPath = downloads.txt || downloads.md;
  await ensureNotebook(page, item, args);
  if (await detectLoginRequired(page)) {
    throw new Error("Google login is required. Run with --setup-login first, then retry.");
  }
  if (!textPath || !fs.existsSync(textPath)) {
    throw new Error(`Local Source Pack file is missing for ${item.job_key || item.title}`);
  }
  if (args.method === "file") {
    return addFileSource(page, item, downloads.md || textPath);
  }
  const text = fs.readFileSync(textPath, "utf8");
  return addCopiedTextSource(page, item, text);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  args.cacheDir = expandHome(args.cacheDir);
  args.chromeProfile = expandHome(args.chromeProfile);
  const queuePath = path.join(args.cacheDir, "upload_queue.json");
  const queue = readJson(queuePath, { items: [] });

  const items = queueItems(queue, args);
  if (!items.length && !args.setupLogin) {
    console.log("No queued NotebookLM uploads.");
    return;
  }

  if (args.dryRun) {
    for (const item of items) {
      console.log(`[dry-run] ${item.job_key || "-"} ${item.title || ""}`);
    }
    return;
  }

  const { chromium } = loadPlaywright();
  const session = await openNotebookBrowser(args, chromium);
  const page = session.page;
  try {
    if (args.setupLogin) {
      await page.goto(DEFAULT_NOTEBOOKLM_URL, { waitUntil: "domcontentloaded", timeout: 60000 });
      console.log("Chrome is open. Finish Google/NotebookLM login in that window.");
      await promptEnter("Press Enter here after NotebookLM is open and logged in...");
      return;
    }

    for (const item of items) {
      const startedAt = new Date().toISOString();
      try {
        console.log(`Uploading ${item.job_key || "-"} ${item.title || ""}`);
        item.status = "uploading";
        item.upload_started_at = startedAt;
        writeJson(queuePath, queue);

        const result = await uploadItem(page, item, args);
        item.status = "uploaded";
        item.uploaded_at = new Date().toISOString();
        item.notebooklm_url = page.url();
        item.upload_method = result.upload_method;
        item.source_title = result.source_title;
        if (result.drive_file_id) item.drive_file_id = result.drive_file_id;
        if (result.drive_url) item.drive_url = result.drive_url;
        delete item.error;
        writeJson(queuePath, queue);
        console.log(`Uploaded ${item.job_key || item.title}: ${item.notebooklm_url}`);
      } catch (error) {
        item.status = "upload_failed";
        item.error = error.stack || error.message || String(error);
        item.failed_at = new Date().toISOString();
        writeJson(queuePath, queue);
        console.error(`Upload failed for ${item.job_key || item.title}: ${error.message || error}`);
        process.exitCode = 1;
      }
    }
  } finally {
    await session.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
