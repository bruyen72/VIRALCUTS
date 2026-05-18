"""Audio transcription — Groq Whisper v3 (primary) or Hugging Face (fallback)."""
import os, requests


def transcribe_video(audio_path: str, provider: str = 'groq', api_key: str = '') -> dict:
    """
    Transcribe audio file.
    Returns: { text, segments: [{start, end, text}], _demo: bool }
    """
    if provider == 'groq' and api_key:
        try:
            return _groq(audio_path, api_key)
        except Exception as e:
            print(f'[Transcriber] Groq failed: {e}')

    if provider == 'hf':
        try:
            return _hf(audio_path, api_key)
        except Exception as e:
            print(f'[Transcriber] HF failed: {e}')

    return _demo()


# ── Groq Whisper Large v3 ─────────────────────────────────────────────

def _groq(path: str, api_key: str) -> dict:
    url     = 'https://api.groq.com/openai/v1/audio/transcriptions'
    headers = {'Authorization': f'Bearer {api_key}'}

    # Groq has a 25 MB file size limit — check first
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > 24:
        raise ValueError(f'Arquivo muito grande para Groq ({size_mb:.1f} MB > 24 MB). Use HF ou reduza o áudio.')

    with open(path, 'rb') as f:
        resp = requests.post(
            url,
            headers=headers,
            files={'file': (os.path.basename(path), f, _mime(path))},
            data={
                'model':           'whisper-large-v3',
                'response_format': 'verbose_json',
                'language':        'pt',
                'temperature':     '0',
            },
            timeout=180
        )

    resp.raise_for_status()
    data = resp.json()

    segments = [
        {'start': s['start'], 'end': s['end'], 'text': s['text'].strip()}
        for s in data.get('segments', [])
        if s.get('text', '').strip()
    ]

    return {'text': data.get('text', ''), 'segments': segments}


# ── Hugging Face Inference API ────────────────────────────────────────

def _hf(path: str, api_key: str = '') -> dict:
    url     = 'https://api-inference.huggingface.co/models/openai/whisper-large-v3'
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}

    with open(path, 'rb') as f:
        data = f.read()

    resp = requests.post(url, headers=headers, data=data, timeout=180)
    resp.raise_for_status()
    res  = resp.json()
    text = res.get('text', '')

    # HF doesn't return segments — build one big segment
    return {
        'text':     text,
        'segments': [{'start': 0.0, 'end': 30.0, 'text': text}],
    }


# ── Demo (no API key) ─────────────────────────────────────────────────

def _demo() -> dict:
    segs = [
        {'start':  0.0, 'end':  4.0, 'text': 'Você nunca vai acreditar nisso!'},
        {'start':  4.0, 'end':  8.5, 'text': 'A regra número um do dinheiro'},
        {'start':  8.5, 'end': 13.0, 'text': 'que ninguém te ensina na escola.'},
        {'start': 13.0, 'end': 18.0, 'text': 'E aí ele disse algo incrível:'},
        {'start': 18.0, 'end': 23.0, 'text': 'Pare de perder tempo e foque!'},
        {'start': 23.0, 'end': 28.0, 'text': 'Quando eu estava no fundo do poço,'},
        {'start': 28.0, 'end': 33.0, 'text': 'descobri esse segredo poderoso.'},
        {'start': 33.0, 'end': 38.0, 'text': 'Você também pode chegar lá.'},
        {'start': 38.0, 'end': 43.0, 'text': 'Só precisa dar o primeiro passo!'},
        {'start': 43.0, 'end': 47.0, 'text': 'Salva esse vídeo e compartilha!'},
    ]
    return {
        'text':     ' '.join(s['text'] for s in segs),
        'segments': segs,
        '_demo':    True,
    }


def _mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {'.wav': 'audio/wav', '.mp3': 'audio/mpeg',
            '.m4a': 'audio/mp4', '.ogg': 'audio/ogg'}.get(ext, 'audio/wav')
