const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    isElectron:    true,
    BACKEND_URL:   'http://127.0.0.1:5050',

    // Window controls
    minimize:      ()       => ipcRenderer.send('window-minimize'),
    maximize:      ()       => ipcRenderer.send('window-maximize'),
    close:         ()       => ipcRenderer.send('window-close'),
    openExternal:  (url)    => ipcRenderer.send('open-external', url),

    // Window state listener
    onWindowState: (cb)     => ipcRenderer.on('window-state', (_, state) => cb(state)),

    // File system (via native dialog)
    selectVideo:   ()       => ipcRenderer.invoke('select-video'),
    saveFile:      (name)   => ipcRenderer.invoke('save-file', name),
    getFilePath:   (p)      => ipcRenderer.invoke('get-file-path', p),
    fileExists:    (p)      => ipcRenderer.invoke('file-exists', p),
});
