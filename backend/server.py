"""ViralCuts Backend - Flask API"""
import os, sys, uuid, threading, json

# Force UTF-8 stdout so special chars (arrows, emoji) don't crash on Windows CP1252
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

jobs: dict = {}

# ── Load API keys from config.json ───────────────────────────────────
_cfg_path = os.path.join(BASE_DIR, 'config.json')
api_keys: dict = {}
if os.path.exists(_cfg_path):
    with open(_cfg_path, 'r') as _f:
        api_keys = json.load(_f)


# ── Health ────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': '2.0.0'})


# ── API Keys ──────────────────────────────────────────────────────────
@app.route('/api/keys', methods=['GET'])
def get_keys():
    return jsonify({k: bool(v) for k, v in api_keys.items()})

@app.route('/api/keys', methods=['POST'])
def save_keys():
    data = request.json or {}
    api_keys.update({k: v for k, v in data.items() if v})
    with open(_cfg_path, 'w') as f:
        json.dump(api_keys, f, indent=2)
    return jsonify({'ok': True})


# ── Register local file (Electron native dialog) ──────────────────────
@app.route('/api/register-path', methods=['POST'])
def register_path():
    path = (request.json or {}).get('path', '')
    if not os.path.exists(path):
        return jsonify({'error': 'Arquivo não encontrado'}), 404
    return jsonify({'path': path, 'ok': True})


# ── Upload (browser mode) ─────────────────────────────────────────────
@app.route('/api/upload', methods=['POST'])
def upload_video():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400
    f    = request.files['file']
    ext  = os.path.splitext(f.filename)[1].lower() or '.mp4'
    name = f'{uuid.uuid4().hex}{ext}'
    path = os.path.join(UPLOAD_DIR, name)
    f.save(path)
    return jsonify({'path': path, 'name': name})


# ── Process (full pipeline) ───────────────────────────────────────────
@app.route('/api/process', methods=['POST'])
def start_process():
    data       = request.json or {}
    video_path = data.get('path', '')
    settings   = data.get('settings', {})

    if not os.path.exists(video_path):
        return jsonify({'error': 'Arquivo não encontrado'}), 404

    job_id = str(uuid.uuid4())
    jobs[job_id] = {'status': 'pending', 'progress': 0, 'message': 'Iniciando...'}

    t = threading.Thread(target=_pipeline, args=(job_id, video_path, settings), daemon=True)
    t.start()
    return jsonify({'job_id': job_id})

@app.route('/api/process/<job_id>')
def poll_job(job_id):
    return jsonify(jobs.get(job_id, {'status': 'not_found'}))


# ── Export single clip ────────────────────────────────────────────────
@app.route('/api/export', methods=['POST'])
def export_clip():
    import shutil
    data       = request.json or {}
    out_path   = data.get('output_path') or os.path.join(OUTPUT_DIR, f'short_{uuid.uuid4().hex[:8]}.mp4')

    # ── Fast path: pipeline already generated a clip with burned subtitles ──
    # If the frontend passes source_clip (already processed), just copy it.
    source_clip = data.get('source_clip', '')
    if source_clip and os.path.exists(source_clip):
        print(f'[Export] Copiando clipe existente (com legenda) -> {os.path.basename(out_path)}')
        shutil.copy2(source_clip, out_path)
        return jsonify({'ok': True, 'output': out_path})

    # ── Slow path: re-process from original video ──
    video_path = data.get('path', '')
    start      = float(data.get('start', 0))
    end        = float(data.get('end', 30))
    srt_path   = data.get('srt_path', '')
    style      = data.get('style', 'white')
    font_size  = int(data.get('font_size', 22))

    if not os.path.exists(video_path):
        return jsonify({'error': 'Arquivo nao encontrado: ' + video_path}), 404

    try:
        from processor import cut_clip_with_subtitles, cut_clip_9_16
        if srt_path and os.path.exists(srt_path):
            print(f'[Export] Re-cortando | SRT={os.path.basename(srt_path)} | estilo={style} | font={font_size}px')
            cut_clip_with_subtitles(video_path, start, end, srt_path, out_path, style, font_size)
        else:
            print(f'[Export] Re-cortando SEM legenda (srt_path={repr(srt_path)} existe={os.path.exists(srt_path) if srt_path else "N/A"})')
            cut_clip_9_16(video_path, start, end, out_path)
        return jsonify({'ok': True, 'output': out_path})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Serve local file ──────────────────────────────────────────────────
@app.route('/api/file')
def serve_file():
    path = request.args.get('path', '')
    if not os.path.exists(path):
        return jsonify({'error': 'não encontrado'}), 404
    return send_file(path)


# ── Serve subtitle (VTT/SRT) with correct CORS headers ────────────────
@app.route('/api/subtitle')
def serve_subtitle():
    path = request.args.get('path', '')
    if not os.path.exists(path):
        return '', 404
    mime = 'text/vtt' if path.endswith('.vtt') else 'text/plain'
    resp = send_file(path, mimetype=mime)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


# ── VideoFala: gerar vídeo falante com mídia do usuário ──────────────
@app.route('/api/gerar', methods=['POST'])
def api_gerar():
    import subprocess, tempfile, time

    texto = (request.form.get('texto') or '').strip()
    midia = request.files.get('midia')

    if not texto:
        return jsonify({'erro': 'Texto não pode estar vazio'}), 400
    if not midia:
        return jsonify({'erro': 'Envie um vídeo ou imagem'}), 400

    groq_key = api_keys.get('groq', '') or os.environ.get('GROQ_API_KEY', '')
    did_key  = api_keys.get('did',  '') or os.environ.get('DID_API_KEY',  '')

    if not groq_key:
        return jsonify({'erro': 'GROQ_API_KEY não configurada. Vá em APIs Gratuitas e salve sua chave Groq.'}), 400
    if not did_key:
        return jsonify({'erro': 'DID_API_KEY não configurada. Vá em APIs Gratuitas e salve sua chave D-ID.'}), 400

    try:
        import requests as req_lib
    except ImportError:
        return jsonify({'erro': 'Pacote "requests" não instalado. Execute: pip install requests'}), 500

    audio_path = None
    midia_path = None
    try:
        # 1 — Reescrever texto com Groq
        print('[VideoFala 1/5] Reescrevendo com Groq...')
        r = req_lib.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
            json={
                'model': 'llama3-8b-8192',
                'messages': [
                    {'role': 'system', 'content': 'Você é um narrador animado e informal. Reescreva o texto como narração curta e envolvente, máximo 4 frases, tom amigável. Responda APENAS com o texto reescrito.'},
                    {'role': 'user',   'content': texto}
                ],
                'max_tokens': 300, 'temperature': 0.8
            },
            timeout=30
        )
        r.raise_for_status()
        texto_gerado = r.json()['choices'][0]['message']['content'].strip()

        # 2 — Gerar áudio com gTTS
        print('[VideoFala 2/5] Gerando áudio...')
        audio_path = os.path.join(tempfile.gettempdir(), f'vf_audio_{uuid.uuid4().hex}.mp3')
        tts_script = os.path.join(os.path.dirname(BASE_DIR), 'tts.py')
        proc = subprocess.run(
            [sys.executable, tts_script, texto_gerado, audio_path],
            capture_output=True, text=True, timeout=30
        )
        if proc.returncode != 0:
            raise RuntimeError(f'gTTS falhou: {proc.stderr}')

        # 3 — Salvar mídia do usuário e fazer upload no D-ID
        print('[VideoFala 3/5] Enviando mídia para D-ID...')
        ext = os.path.splitext(midia.filename)[1].lower() or '.jpg'
        midia_path = os.path.join(tempfile.gettempdir(), f'vf_midia_{uuid.uuid4().hex}{ext}')
        midia.save(midia_path)

        mime = midia.content_type or ('video/mp4' if ext in ('.mp4','.mov','.avi') else 'image/jpeg')
        did_endpoint = 'videos' if mime.startswith('video') else 'images'
        field_name   = 'video'  if mime.startswith('video') else 'image'

        with open(midia_path, 'rb') as mf:
            up = req_lib.post(
                f'https://api.d-id.com/{did_endpoint}',
                headers={'Authorization': f'Basic {did_key}'},
                files={field_name: (os.path.basename(midia_path), mf, mime)},
                timeout=60
            )
        up.raise_for_status()
        source_url = up.json().get('url') or up.json().get('id')
        if not source_url:
            raise RuntimeError(f'D-ID não retornou URL da mídia: {up.text}')

        # 4 — Upload do áudio no D-ID
        print('[VideoFala 4/5] Enviando áudio...')
        with open(audio_path, 'rb') as af:
            ua = req_lib.post(
                'https://api.d-id.com/audios',
                headers={'Authorization': f'Basic {did_key}'},
                files={'audio': ('audio.mp3', af, 'audio/mpeg')},
                timeout=60
            )
        ua.raise_for_status()
        audio_url = ua.json()['url']

        # 5 — Criar talk no D-ID com a mídia do usuário
        print('[VideoFala 5/5] Criando talk...')
        tk = req_lib.post(
            'https://api.d-id.com/talks',
            headers={'Authorization': f'Basic {did_key}', 'Content-Type': 'application/json'},
            json={'source_url': source_url, 'script': {'type': 'audio', 'audio_url': audio_url}},
            timeout=30
        )
        tk.raise_for_status()
        talk_id = tk.json()['id']

        # 6 — Polling até concluir
        deadline = time.time() + 90
        video_url = None
        while time.time() < deadline:
            st = req_lib.get(
                f'https://api.d-id.com/talks/{talk_id}',
                headers={'Authorization': f'Basic {did_key}'}, timeout=15
            ).json()
            if st.get('status') == 'done' and st.get('result_url'):
                video_url = st['result_url']; break
            if st.get('status') == 'error':
                raise RuntimeError('D-ID retornou erro ao processar vídeo')
            time.sleep(3)

        if not video_url:
            raise RuntimeError('Timeout: vídeo demorou mais de 90 segundos')

        print(f'[VideoFala] Concluído: {video_url}')
        return jsonify({'videoUrl': video_url, 'textoGerado': texto_gerado})

    except Exception as e:
        print(f'[VideoFala] ERRO: {e}')
        return jsonify({'erro': str(e)}), 500
    finally:
        for p in [audio_path, midia_path]:
            if p and os.path.exists(p):
                try: os.remove(p)
                except: pass


# ── Pipeline worker ───────────────────────────────────────────────────
def _upd(job_id, **kw):
    jobs[job_id].update(kw)

def _check_libass():
    """Returns True if FFmpeg was compiled with libass (needed for subtitle burn)."""
    import subprocess
    try:
        r = subprocess.run(['ffmpeg', '-filters'], capture_output=True, text=True)
        has = 'subtitles' in r.stdout
        print(f'[Pipeline] libass/subtitles filter disponível: {has}')
        return has
    except Exception:
        return False


def _pipeline(job_id, video_path, settings):
    groq_key        = settings.get('groqKey') or api_keys.get('groq', '')
    hf_key          = settings.get('hfKey')   or api_keys.get('hf', '')
    target_duration = float(settings.get('target_duration', 45))   # user-chosen clip length
    max_clips       = int(settings.get('max_clips', 5))            # max number of clips
    platform        = settings.get('platform', 'tiktok')
    print(f'[Pipeline] Configuracao: dur={target_duration}s, clips={max_clips}, plataforma={platform}')

    try:
        # 1 — Extract audio
        _upd(job_id, status='processing', progress=5, message='Extraindo áudio...')
        from processor import extract_audio, get_video_info
        audio_path = extract_audio(video_path)
        info       = get_video_info(video_path)
        has_libass = _check_libass()
        print(f'[Pipeline] Vídeo: {os.path.basename(video_path)} | {info}')
        if not has_libass:
            print('[Pipeline] WARN: FFmpeg sem libass -- legendas serao sobrepostas em JS, nao queimadas no MP4')

        # 2 — Transcribe
        _upd(job_id, progress=20, message='Transcrevendo com Whisper IA...')
        from transcriber import transcribe_video
        provider   = 'groq' if groq_key else ('hf' if hf_key else 'demo')
        transcript = transcribe_video(audio_path, provider, groq_key or hf_key)
        segments   = transcript.get('segments', [])

        # 3 — Generate full SRT + VTT
        _upd(job_id, progress=35, message='Gerando legendas...')
        from srt_generator import write_both, split_long_segments, clip_segments
        segments  = split_long_segments(segments, max_chars=50)
        full_base = os.path.join(OUTPUT_DIR, f'full_{uuid.uuid4().hex[:8]}.srt')
        write_both(segments, full_base, offset=0.0)

        # 4 — LLM finds viral moments
        _upd(job_id, progress=50, message='Analisando momentos virais com LLM...')
        from analyzer import find_viral_moments
        moments = find_viral_moments(transcript, groq_key)

        # Limit to user-chosen max_clips
        moments = moments[:max_clips]
        print(f'[Pipeline] {len(moments)} momentos virais selecionados (max={max_clips})')

        # 5 — Build clips with burned subtitles
        _upd(job_id, progress=65, message='Cortando clipes 9:16 e queimando legendas...')
        results = []
        total   = len(moments)
        vid_dur = info.get('duration', 9999)

        for i, m in enumerate(moments):
            start = float(m.get('start', 0))
            # Enforce target_duration: ignore LLM's end, use start + target
            end   = start + target_duration
            end   = min(end, vid_dur)
            # Safety: if remaining video is too short, skip
            if end - start < 5:
                print(f'[Pipeline] Clipe {i+1} ignorado (muito curto: {end-start:.1f}s)')
                continue

            # Build per-clip SRT + VTT (timestamps relative to clip start)
            clip_segs  = clip_segments(segments, start, end)
            clip_base  = os.path.join(OUTPUT_DIR, f'clip{i+1}_{uuid.uuid4().hex[:6]}.srt')
            clip_files = write_both(clip_segs, clip_base, offset=start)
            clip_srt   = clip_files['srt']
            clip_vtt   = clip_files['vtt']
            print(f'[Pipeline] Clipe {i+1}: {start:.1f}s->{end:.1f}s | SRT={os.path.basename(clip_srt)} | segs={len(clip_segs)}')

            # Export clip with burned subtitles (FFmpeg -t duration fix)
            out = os.path.join(OUTPUT_DIR, f'short_{i+1}_{uuid.uuid4().hex[:6]}.mp4')
            subtitle_burned = False
            try:
                from processor import cut_clip_with_subtitles
                cut_clip_with_subtitles(video_path, start, end, clip_srt, out, style='white')
                subtitle_burned = True
                print(f'[Pipeline] OK Legenda QUEIMADA no clipe {i+1}: {os.path.basename(out)}')
            except Exception as ex:
                print(f'[Pipeline] WARN: FALHOU queimar legenda no clipe {i+1}: {ex}')
                print(f'[Pipeline]      Tentando corte simples sem legenda...')
                try:
                    from processor import cut_clip_9_16
                    cut_clip_9_16(video_path, start, end, out)
                    print(f'[Pipeline] OK Clipe {i+1} gerado SEM legenda (fallback)')
                except Exception as ex2:
                    print(f'[Pipeline] ERRO: Corte simples tambem falhou: {ex2}')
                    out = video_path  # fallback to original
            print(f'[Pipeline] Clipe {i+1} → subtitle_burned={subtitle_burned} | arquivo={os.path.basename(out)}')

            prog = 65 + int(((i + 1) / total) * 30)
            _upd(job_id, progress=prog, message=f'Clipe {i+1}/{total} gerado...')

            dur_s = int(end - start)

            # Normalize segment timestamps to be relative to clip start (t=0).
            # The exported MP4 starts at t=0, so the frontend overlay sync must
            # use clip-relative times — NOT the original video timestamps.
            rel_segs = [
                {
                    'start': max(0.0, s['start'] - start),
                    'end':   max(0.1, s['end']   - start),
                    'text':  s['text'],
                }
                for s in clip_segs
            ]

            vtt_url = (
                f'http://127.0.0.1:5050/api/subtitle?path='
                + clip_vtt.replace('\\', '/').replace(':', '%3A')
            )

            results.append({
                'id':              i + 1,
                'title':           m.get('title', f'Clipe {i+1}'),
                'hook':            m.get('hook', ''),
                'score':           m.get('score', 80),
                'start':           start,
                'end':             end,
                'duration':        f'0:{dur_s:02d}',
                'video':           out,
                'srt':             clip_srt,
                'vtt':             clip_vtt,
                'vtt_url':         vtt_url,
                'segments':        rel_segs,
                'reason':          m.get('reason', ''),
                'has_burned_subs': subtitle_burned,  # True = legenda ja esta no MP4
            })

        _upd(job_id, status='done', progress=100, message='Concluído!',
             results=results, transcript=transcript)

    except Exception as e:
        import traceback
        traceback.print_exc()
        _upd(job_id, status='error', progress=0, message=str(e), error=str(e))


if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5050))
    print(f'ViralCuts Backend v2 — porta {port}')
    from flask import Flask
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
