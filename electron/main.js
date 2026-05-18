const { app, BrowserWindow, ipcMain, dialog, shell, nativeTheme, protocol } = require('electron');
const path  = require('path');
const { spawn } = require('child_process');
const fs    = require('fs');
const http  = require('http');

// MIME types for local file serving
const MIME = {
    '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.avi': 'video/x-msvideo',
    '.mkv': 'video/x-matroska', '.webm': 'video/webm', '.m4v': 'video/mp4',
    '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg',
    '.vtt': 'text/vtt', '.srt': 'text/plain',
};

// Stream a local file to a WHATWG ReadableStream (required by protocol.handle)
function fileToWebStream(filePath, start, end) {
    const nodeStream = fs.createReadStream(filePath, start != null ? { start, end } : {});
    return new ReadableStream({
        start(ctrl) {
            nodeStream.on('data',  chunk => ctrl.enqueue(chunk instanceof Buffer ? chunk : Buffer.from(chunk)));
            nodeStream.on('end',   ()    => ctrl.close());
            nodeStream.on('error', err  => ctrl.error(err));
        },
        cancel() { nodeStream.destroy(); }
    });
}

// Serve a local file with proper byte-range support (needed for video/audio streaming)
function serveLocalFile(filePath, rangeHeader) {
    if (!fs.existsSync(filePath)) {
        console.error(`[localfile] ARQUIVO NAO ENCONTRADO: ${filePath}`);
        return new Response('File not found', { status: 404 });
    }

    const ext         = path.extname(filePath).toLowerCase();
    const contentType = MIME[ext] || 'application/octet-stream';
    const fileSize    = fs.statSync(filePath).size;

    if (rangeHeader) {
        const m = rangeHeader.match(/bytes=(\d+)-(\d*)/);
        if (m) {
            const start = parseInt(m[1], 10);
            const end   = m[2] ? parseInt(m[2], 10) : fileSize - 1;
            const chunk = end - start + 1;
            console.log(`[localfile] 206 Range ${start}-${end}/${fileSize} | ${path.basename(filePath)}`);
            return new Response(fileToWebStream(filePath, start, end), {
                status: 206,
                headers: {
                    'Content-Range':  `bytes ${start}-${end}/${fileSize}`,
                    'Accept-Ranges':  'bytes',
                    'Content-Length': String(chunk),
                    'Content-Type':   contentType,
                    'Cache-Control':  'no-store',
                },
            });
        }
    }

    console.log(`[localfile] 200 Full ${fileSize}B | ${path.basename(filePath)}`);
    return new Response(fileToWebStream(filePath), {
        status: 200,
        headers: {
            'Accept-Ranges':  'bytes',
            'Content-Length': String(fileSize),
            'Content-Type':   contentType,
            'Cache-Control':  'no-store',
        },
    });
}

let mainWindow;
let pythonProcess;
const BACKEND_PORT = 5050;

// ── Start Python backend ──────────────────────────────────────────────
function startPythonBackend() {
    const serverPath = path.join(__dirname, '..', 'backend', 'server.py');
    const pythonCmd  = process.platform === 'win32' ? 'python' : 'python3';

    pythonProcess = spawn(pythonCmd, [serverPath], {
        cwd: path.join(__dirname, '..', 'backend'),
        stdio: ['ignore', 'pipe', 'pipe']
    });

    pythonProcess.stdout.on('data', d => console.log('[Backend]', d.toString().trim()));
    pythonProcess.stderr.on('data', d => console.error('[Backend ERR]', d.toString().trim()));

    pythonProcess.on('close', code => {
        console.log(`[Backend] encerrado com código ${code}`);
    });
}

// ── Wait for backend ──────────────────────────────────────────────────
function waitForBackend(retries = 20) {
    return new Promise((resolve, reject) => {
        const attempt = (n) => {
            const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, res => {
                resolve();
            });
            req.on('error', () => {
                if (n <= 0) { reject(new Error('Backend não iniciou')); return; }
                setTimeout(() => attempt(n - 1), 500);
            });
            req.setTimeout(400, () => { req.destroy(); });
        };
        attempt(retries);
    });
}

// ── Create window ─────────────────────────────────────────────────────
function createWindow() {
    nativeTheme.themeSource = 'dark';

    mainWindow = new BrowserWindow({
        width:      1440,
        height:     900,
        minWidth:   1024,
        minHeight:  680,
        frame:      false,
        transparent: false,
        backgroundColor: '#030306',
        show:       false,
        webPreferences: {
            preload:          path.join(__dirname, 'preload.js'),
            nodeIntegration:  false,
            contextIsolation: true,
            webSecurity:      true,   // keep security on
            allowRunningInsecureContent: false,
        },
        titleBarStyle: 'hidden',
    });

    mainWindow.loadFile(path.join(__dirname, '..', 'index.html'));

    mainWindow.once('ready-to-show', () => {
        mainWindow.show();
    });

    mainWindow.on('maximize',   () => mainWindow.webContents.send('window-state', 'maximized'));
    mainWindow.on('unmaximize', () => mainWindow.webContents.send('window-state', 'normal'));
    mainWindow.on('closed',     () => { mainWindow = null; });
}

// ── App lifecycle ─────────────────────────────────────────────────────
app.whenReady().then(async () => {

    // localfile:/// protocol — streams local files with proper byte-range support.
    // Uses fs.createReadStream instead of net.fetch because net.fetch does NOT
    // buffer-then-stream properly for large video files, causing MEDIA_ERR_SRC_NOT_SUPPORTED.
    // Triple slash keeps Windows drive letter (C:) in the URL path, not the host.
    protocol.handle('localfile', (request) => {
        const filePath = decodeURIComponent(request.url.replace('localfile:///', ''));
        const range    = request.headers.get('Range');
        try {
            return serveLocalFile(filePath, range);
        } catch (err) {
            console.error(`[localfile] ERRO interno: ${err.message}`);
            return new Response('Internal error', { status: 500 });
        }
    });

    startPythonBackend();
    try {
        await waitForBackend();
        console.log('[App] Backend pronto');
    } catch (e) {
        console.warn('[App] Backend não respondeu, continuando sem ele');
    }
    createWindow();
});

app.on('window-all-closed', () => {
    if (pythonProcess) { pythonProcess.kill('SIGTERM'); }
    if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// ── IPC handlers ──────────────────────────────────────────────────────
ipcMain.handle('select-video', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
        title:      'Selecionar Vídeo',
        properties: ['openFile'],
        filters:    [
            { name: 'Vídeos', extensions: ['mp4','mov','avi','mkv','webm','m4v'] }
        ]
    });
    return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('save-file', async (_, defaultName) => {
    const result = await dialog.showSaveDialog(mainWindow, {
        defaultPath: defaultName || 'short.mp4',
        filters:     [{ name: 'Vídeo MP4', extensions: ['mp4'] }]
    });
    return result.canceled ? null : result.filePath;
});

ipcMain.handle('get-file-path', async (_, filePath) => {
    // Use localfile:/// (triple slash) — double slash would make Chromium treat
    // the Windows drive letter as a hostname and strip the colon.
    return filePath ? `localfile:///${filePath.replace(/\\/g, '/')}` : null;
});

ipcMain.handle('file-exists', async (_, filePath) => {
    return fs.existsSync(filePath);
});

ipcMain.on('window-minimize',  () => mainWindow?.minimize());
ipcMain.on('window-maximize',  () => mainWindow?.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize());
ipcMain.on('window-close',     () => { if (pythonProcess) pythonProcess.kill('SIGTERM'); mainWindow?.close(); });
ipcMain.on('open-external',    (_, url) => shell.openExternal(url));
