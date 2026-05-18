require('dotenv').config();
const express = require('express');
const multer  = require('multer');
const Groq    = require('groq-sdk');
const path    = require('path');
const os      = require('os');
const fs      = require('fs');
const { execFile } = require('child_process');

const app    = express();
const PORT   = process.env.PORT || 3001;
const groq   = new Groq({ apiKey: process.env.GROQ_API_KEY });

const TMP      = os.tmpdir();
const VIDEO_IN = path.join(TMP, 'vf_input.mp4');
const AUDIO    = path.join(TMP, 'vf_audio.mp3');
const VIDEO_OUT= path.join(TMP, 'vf_output.mp4');
const TTS_PY   = path.join(__dirname, 'tts.py');

app.use((req, res, next) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', '*');
    next();
});
app.use(express.static(path.join(__dirname, 'public')));

const upload = multer({ dest: TMP });

// ── POST /api/gerar ──────────────────────────────────────────────────
app.post('/api/gerar', upload.single('video'), async (req, res) => {
    const texto = (req.body.texto || '').trim();
    const file  = req.file;

    if (!texto) return res.status(400).json({ erro: 'Texto não pode estar vazio' });
    if (!file)  return res.status(400).json({ erro: 'Envie um arquivo de vídeo (.mp4)' });
    if (!process.env.GROQ_API_KEY) return res.status(400).json({ erro: 'GROQ_API_KEY não configurada no .env' });

    try {
        // 1 — Mover vídeo para caminho fixo
        fs.copyFileSync(file.path, VIDEO_IN);
        fs.unlinkSync(file.path);

        // 2 — Reescrever texto com Groq
        console.log('[1/4] Reescrevendo com Groq...');
        const chat = await groq.chat.completions.create({
            model: 'llama-3.1-8b-instant',
            messages: [
                { role: 'system', content: 'Você é um narrador animado e informal. Reescreva o texto como narração curta e envolvente, máximo 4 frases, tom amigável e direto. Responda APENAS com o texto reescrito.' },
                { role: 'user',   content: texto }
            ],
            max_tokens: 300,
            temperature: 0.8
        });
        const textoGerado = chat.choices[0].message.content.trim();

        // 3 — Gerar áudio com edge-tts
        console.log('[2/4] Gerando áudio com edge-tts...');
        await new Promise((resolve, reject) => {
            execFile('python', [TTS_PY, textoGerado, AUDIO], { timeout: 30000 }, (err, stdout, stderr) => {
                if (err) return reject(new Error('edge-tts falhou: ' + stderr));
                resolve();
            });
        });

        // 4 — Juntar vídeo + áudio com ffmpeg
        console.log('[3/4] Juntando vídeo + áudio com ffmpeg...');
        await new Promise((resolve, reject) => {
            execFile('ffmpeg', [
                '-y',
                '-i', VIDEO_IN,
                '-i', AUDIO,
                '-map', '0:v',
                '-map', '1:a',
                '-c:v', 'copy',
                '-shortest',
                VIDEO_OUT
            ], { timeout: 120000 }, (err, stdout, stderr) => {
                if (err) return reject(new Error('ffmpeg falhou: ' + stderr.slice(-300)));
                resolve();
            });
        });

        console.log('[4/4] Concluído!');
        res.json({ ok: true, textoGerado });

    } catch (err) {
        console.error('[VideoFala] ERRO:', err.message);
        res.status(500).json({ erro: err.message });
    } finally {
        [VIDEO_IN, AUDIO].forEach(p => { try { if (fs.existsSync(p)) fs.unlinkSync(p); } catch(_){} });
    }
});

// ── GET /download ────────────────────────────────────────────────────
app.get('/download', (req, res) => {
    if (!fs.existsSync(VIDEO_OUT)) return res.status(404).json({ erro: 'Nenhum vídeo gerado ainda' });
    res.download(VIDEO_OUT, 'video_final.mp4', err => {
        if (!err) { try { fs.unlinkSync(VIDEO_OUT); } catch(_){} }
    });
});

app.listen(PORT, () => console.log(`\n🎙️  VideoFala rodando em http://localhost:${PORT}\n`));
