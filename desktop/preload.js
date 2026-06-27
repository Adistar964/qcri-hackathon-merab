/**
 * Preload — the only bridge between the renderer (the Next.js UI) and Electron.
 * Exposes a tiny, safe API. The mere presence of window.fanarDesktop tells the UI
 * to run in "desktop" surface mode (enabling screen-vision + computer-control).
 */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("fanarDesktop", {
  isDesktop: true,
  minimize: () => ipcRenderer.send("win:minimize"),
  close: () => ipcRenderer.send("win:close"),
});
