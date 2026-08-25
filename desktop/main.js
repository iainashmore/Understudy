// The Electron shell around Understudy.
//
// The application is a local HTTP server and a web page; this is a window for
// it. Electron rather than a WebView2 shell for one reason: it carries its own
// Chromium, so the window renders on a locked-down machine where the WebView2
// runtime has been stripped or blocked. On a CAD workstation that is not a
// hypothetical, and it is the failure that would leave somebody with nothing.
//
// Two rules hold this together:
//
//   * The server is a plain HTTP server on the loopback interface. If the
//     window never appears, the URL still works in any browser, and the
//     console says so. The app degrades to a link rather than failing.
//   * Closing the window stops the server. An orphaned Python process holding
//     port 8765 is the sidecar bug everybody writes at least once.

const { app, BrowserWindow, shell, dialog, Menu, clipboard } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");
const os = require("os");

const HOST = "127.0.0.1";
const START_PORT = 8765;
//: The server has to import numpy and Playwright before it listens; on a cold
//: Windows disk that is slower than anyone expects.
const READY_TIMEOUT_MS = 60_000;

let serverProcess = null;
let mainWindow = null;
let serverUrl = "";
const log = [];

function note(line) {
  const stamped = `${new Date().toISOString()} ${line}`;
  log.push(stamped);
  console.log(stamped);
}

function serverExecutable() {
  // Packaged: next to the app resources. Development: the repo checkout.
  const name = process.platform === "win32" ? "understudy-server.exe" : "understudy-server";
  const packaged = path.join(process.resourcesPath || "", "server", name);
  if (fs.existsSync(packaged)) return { command: packaged, args: [] };

  const repo = path.resolve(__dirname, "..");
  return {
    command: process.platform === "win32" ? "python" : "python3",
    args: ["-m", "understudy.cli", "ui", "--no-open"],
    cwd: repo,
  };
}

function defaultWorkspace() {
  return path.join(os.homedir(), "Understudy");
}

function waitForServer(url, deadline) {
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(url, (response) => {
        response.resume();
        resolve();
      });
      request.on("error", () => {
        if (Date.now() > deadline) {
          reject(new Error("the server did not start in time"));
          return;
        }
        setTimeout(attempt, 250);
      });
      request.setTimeout(2000, () => request.destroy());
    };
    attempt();
  });
}

async function startServer() {
  const workspace = defaultWorkspace();
  fs.mkdirSync(workspace, { recursive: true });

  const { command, args, cwd } = serverExecutable();
  const full = [...args, "--workspace", workspace, "--port", String(START_PORT)];
  note(`starting: ${command} ${full.join(" ")}`);

  serverProcess = spawn(command, full, {
    cwd: cwd || undefined,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  serverProcess.stdout.on("data", (d) => note(`server: ${String(d).trimEnd()}`));
  serverProcess.stderr.on("data", (d) => note(`server: ${String(d).trimEnd()}`));
  serverProcess.on("exit", (code) => {
    note(`server exited with ${code}`);
    serverProcess = null;
  });

  serverUrl = `http://${HOST}:${START_PORT}/`;
  await waitForServer(serverUrl, Date.now() + READY_TIMEOUT_MS);
  note(`server ready on ${serverUrl}`);
}

function stopServer() {
  if (!serverProcess) return;
  note("stopping the server");
  // Windows has no SIGTERM worth the name for a detached console app; taskkill
  // is what actually stops a PyInstaller build and its children.
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(serverProcess.pid), "/f", "/t"]);
  } else {
    serverProcess.kill("SIGTERM");
  }
  serverProcess = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440, height: 940, minWidth: 900, minHeight: 600,
    backgroundColor: "#1c2025",
    title: "Understudy",
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: {
      // The page is served from our own loopback server and needs nothing from
      // Node. Keeping it isolated costs nothing and closes the obvious hole.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  mainWindow.loadURL(serverUrl);
  mainWindow.on("closed", () => { mainWindow = null; });

  // A link to anything else opens in the real browser, not inside the app.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(serverUrl)) shell.openExternal(url);
    return { action: "deny" };
  });
}

function failed(error) {
  note(`could not start: ${error.message}`);
  const detail =
    `${error.message}\n\n` +
    `Understudy is a local server with a web interface, so it may still be ` +
    `running even though this window is not. Try opening:\n\n  ${serverUrl}\n\n` +
    `in any browser.`;
  const choice = dialog.showMessageBoxSync({
    type: "error",
    title: "Understudy could not start",
    message: "The Understudy server did not start.",
    detail,
    buttons: ["Copy the log", "Open the URL anyway", "Quit"],
    defaultId: 0,
  });
  if (choice === 0) clipboard.writeText(log.join("\n"));
  if (choice === 1) shell.openExternal(serverUrl);
  app.quit();
}

function buildMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: "Understudy",
      submenu: [
        { label: "Open in a browser", click: () => shell.openExternal(serverUrl) },
        { label: "Reload", accelerator: "CmdOrCtrl+R",
          click: () => mainWindow && mainWindow.reload() },
        { label: "Developer tools", accelerator: "CmdOrCtrl+Shift+I",
          click: () => mainWindow && mainWindow.webContents.toggleDevTools() },
        { type: "separator" },
        { label: "Copy the startup log", click: () => clipboard.writeText(log.join("\n")) },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { role: "editMenu" },
  ]));
}

// One instance. A second copy would fight the first for the port and for the
// workspace, and the loser's error would be baffling.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) { mainWindow.restore(); mainWindow.focus(); }
  });

  app.whenReady().then(async () => {
    try {
      await startServer();
    } catch (error) {
      failed(error);
      return;
    }
    buildMenu();
    createWindow();
  });

  app.on("window-all-closed", () => { stopServer(); app.quit(); });
  app.on("before-quit", stopServer);
  // Belt and braces: a crash in the shell must not leave the server holding
  // the port, because the next launch would then fail for a different reason.
  process.on("exit", stopServer);
}
