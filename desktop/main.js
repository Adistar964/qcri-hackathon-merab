/**
 * Fanar Agent — Electron desktop shell.
 *
 * This is the "desktop frontend" that unlocks capabilities the web build cannot
 * safely have: the UI runs as a real native app (frameless, global hotkey, tray),
 * and it reports surface="desktop" to the backend, which enables screen-vision and
 * computer-control tools (see backend/desktop.py).
 *
 * It loads the same Next.js UI (http://localhost:3000) so we keep ONE frontend
 * codebase. The Python FastAPI backend (port 8000) is the agent brain and performs
 * the actual screen capture + mouse/keyboard control.
 *
 * Run order for a full session:
 *   1) backend:  uvicorn main:app --port 8000   (with screen/control deps installed)
 *   2) frontend: npm run dev   (Next on :3000)
 *   3) desktop:  npm start      (this app)
 * The helper script scripts/run_desktop.ps1 starts all three.
 */

const { app, BrowserWindow, globalShortcut, Tray, Menu, ipcMain, shell } = require("electron");
const path = require("path");

const UI_URL = process.env.FANAR_UI_URL || "http://localhost:3000";
let win = null;
let tray = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 940,
    minHeight: 640,
    backgroundColor: "#0A1730",
    frame: false, // frameless — custom controls live in the React header
    show: false,
    title: "Fanar Agent",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadURL(UI_URL);
  win.once("ready-to-show", () => win.show());

  // Open external links in the user's browser, not inside the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http")) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  win.on("closed", () => (win = null));
}

function toggleWindow() {
  if (!win) return createWindow();
  if (win.isVisible() && win.isFocused()) win.hide();
  else {
    win.show();
    win.focus();
  }
}

app.whenReady().then(() => {
  createWindow();

  // Global hotkey to summon/dismiss the agent from anywhere.
  globalShortcut.register("CommandOrControl+Shift+F", toggleWindow);

  // System tray.
  try {
    tray = new Tray(path.join(__dirname, "trayTemplate.png"));
    tray.setToolTip("Fanar Agent");
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: "Show / Hide  (Ctrl+Shift+F)", click: toggleWindow },
        { type: "separator" },
        { label: "Quit", click: () => app.quit() },
      ])
    );
    tray.on("click", toggleWindow);
  } catch (e) {
    // Tray icon optional — ignore if the asset is missing.
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// Window control IPC (frameless custom titlebar buttons).
ipcMain.on("win:minimize", () => win && win.minimize());
ipcMain.on("win:close", () => win && win.close());

// Single instance — focus the existing window instead of spawning another.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();
else app.on("second-instance", () => win && (win.show(), win.focus()));

app.on("will-quit", () => globalShortcut.unregisterAll());
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
