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
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization,Accept'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    return response

@app.before_request
def handle_options():
    if request.method == 'OPTIONS':
        from flask import make_response
        res = make_response()
        res.headers['Access-Control-Allow-Origin']  = '*'
        res.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization,Accept'
        res.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
        return res

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


# ── VideoFala: gerar vídeo com narração IA ───────────────────────────
@app.route('/api/gerar', methods=['POST'])
def api_gerar():
    import subprocess, tempfile

    texto = (request.form.get('texto') or '').strip()
    video = request.files.get('video')

    if not texto:
        return jsonify({'erro': 'Texto não pode estar vazio'}), 400
    if not video:
        return jsonify({'erro': 'Envie um arquivo de vídeo .mp4'}), 400

    tmp        = tempfile.gettempdir()
    video_in   = os.path.join(tmp, f'vf_in_{uuid.uuid4().hex}.mp4')
    audio_path = os.path.join(tmp, f'vf_audio_{uuid.uuid4().hex}.mp3')
    video_out  = os.path.join(tmp, f'vf_out_{uuid.uuid4().hex}.mp4')
    tts_script = os.path.join(os.path.dirname(BASE_DIR), 'tts.py')

    try:
        # usa exatamente o texto do usuário — sem reescrita
        texto_gerado = texto

        # 1 — Salvar vídeo e gerar áudio com edge-tts
        print('[VideoFala 1/2] Gerando áudio com edge-tts...')
        video.save(video_in)
        proc = subprocess.run(
            [sys.executable, tts_script, texto_gerado, audio_path],
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=60
        )
        if proc.returncode != 0:
            raise RuntimeError(f'edge-tts falhou: {proc.stderr}')

        # 2 — Juntar vídeo + áudio com ffmpeg (reencoda para garantir sync)
        print('[VideoFala 2/2] Juntando com ffmpeg...')
        ffmpeg_proc = subprocess.run(
            [
                'ffmpeg', '-y',
                '-i', video_in,
                '-i', audio_path,
                '-map', '0:v:0',
                '-map', '1:a:0',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
                '-c:a', 'aac', '-b:a', '128k',
                '-shortest',
                '-async', '1',
                '-vsync', '1',
                video_out,
            ],
            capture_output=True, text=True, timeout=180
        )
        if ffmpeg_proc.returncode != 0:
            raise RuntimeError(f'ffmpeg falhou: {ffmpeg_proc.stderr[-400:]}')

        # Serve o arquivo e apaga depois
        print(f'[VideoFala] Concluído: {video_out}')

        def remover_depois(path):
            import time; time.sleep(60)
            try: os.remove(path)
            except: pass

        threading.Thread(target=remover_depois, args=(video_out,), daemon=True).start()

        resp = send_file(video_out, mimetype='video/mp4',
                         as_attachment=True, download_name='video_final.mp4')
        resp.headers['X-Texto-Gerado'] = texto_gerado
        resp.headers['Access-Control-Expose-Headers'] = 'X-Texto-Gerado'
        return resp

    except Exception as e:
        print(f'[VideoFala] ERRO: {e}')
        return jsonify({'erro': str(e)}), 500
    finally:
        for p in [video_in, audio_path]:
            try:
                if os.path.exists(p): os.remove(p)
            except: pass




# ── VozVídeo: sincronização multi-personagem via sync_video.py ───────
@app.route('/api/sync', methods=['POST'])
def api_sync():
    import subprocess, tempfile, json as _json

    video       = request.files.get('video')
    script_raw  = (request.form.get('script') or '[]').strip()
    stretch     = request.form.get('stretch', '1') == '1'

    if not video:
        return jsonify({'erro': 'Envie um arquivo de vídeo'}), 400

    try:
        script_data = _json.loads(script_raw)
    except Exception:
        return jsonify({'erro': 'Script inválido (JSON esperado)'}), 400

    if not script_data:
        return jsonify({'erro': 'Script sem falas'}), 400

    tmp      = tempfile.mkdtemp(prefix='sync_')
    video_in  = os.path.join(tmp, f'input_{uuid.uuid4().hex}.mp4')
    script_f  = os.path.join(tmp, 'script.json')
    video_out = os.path.join(tmp, 'output.mp4')
    sync_py   = os.path.join(os.path.dirname(BASE_DIR), 'sync_video.py')

    try:
        video.save(video_in)
        with open(script_f, 'w', encoding='utf-8') as f:
            _json.dump(script_data, f, ensure_ascii=False)

        cmd = [
            sys.executable, sync_py,
            '--video',  video_in,
            '--script', script_f,
            '--output', video_out,
            '--tts',    'edge',
            '--lang',   'pt',
        ]
        if not stretch:
            cmd.append('--no-stretch')

        print(f'[Sync] {len(script_data)} falas → {video_out}')
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-500:] or 'sync_video.py falhou')

        if not os.path.exists(video_out):
            raise RuntimeError('Vídeo de saída não foi gerado')

        def _rm(p, delay=120):
            import time; time.sleep(delay)
            try: import shutil; shutil.rmtree(p, ignore_errors=True)
            except: pass

        threading.Thread(target=_rm, args=(tmp,), daemon=True).start()

        resp = send_file(video_out, mimetype='video/mp4',
                         as_attachment=True, download_name='vozvideo_final.mp4')
        resp.headers['Access-Control-Expose-Headers'] = 'X-Falas'
        resp.headers['X-Falas'] = str(len(script_data))
        return resp

    except subprocess.TimeoutExpired:
        return jsonify({'erro': 'Tempo limite excedido (10 min). Use um vídeo menor.'}), 500
    except Exception as e:
        print(f'[Sync] ERRO: {e}')
        return jsonify({'erro': str(e)}), 500


# ── VideoFala: lip-sync com amplitude de áudio ───────────────────────
@app.route('/api/lipsync', methods=['POST'])
def api_lipsync():
    import subprocess, tempfile

    texto    = (request.form.get('texto') or '').strip()
    video    = request.files.get('video')
    boca_raw = request.form.get('boca', '{}')

    if not texto:
        return jsonify({'erro': 'Texto não pode estar vazio'}), 400
    if not video:
        return jsonify({'erro': 'Envie um arquivo de vídeo .mp4'}), 400

    try:
        boca = json.loads(boca_raw)
    except Exception:
        boca = {}

    if not all(k in boca for k in ('x', 'y', 'w', 'h')):
        return jsonify({'erro': 'Marque a região da boca no vídeo antes de gerar.'}), 400

    tmp        = tempfile.gettempdir()
    video_in   = os.path.join(tmp, f'ls_in_{uuid.uuid4().hex}.mp4')
    audio_path = os.path.join(tmp, f'ls_audio_{uuid.uuid4().hex}.mp3')
    video_out  = os.path.join(OUTPUT_DIR, f'lipsync_{uuid.uuid4().hex[:8]}.mp4')
    tts_script = os.path.join(os.path.dirname(BASE_DIR), 'tts.py')

    try:
        # 1 — Salvar vídeo
        video.save(video_in)

        # 2 — Gerar áudio com edge-tts
        print('[LipSync 1/2] Gerando áudio com edge-tts...')
        proc = subprocess.run(
            [sys.executable, tts_script, texto, audio_path],
            capture_output=True, encoding='utf-8', errors='replace', timeout=60
        )
        if proc.returncode != 0:
            raise RuntimeError(f'edge-tts falhou: {proc.stderr}')

        # 3 — Aplicar lip-sync
        print('[LipSync 2/2] Aplicando lip-sync...')
        from lipsync import aplicar_lipsync
        aplicar_lipsync(video_in, audio_path, boca, video_out)

        print(f'[LipSync] Concluído: {video_out}')

        def remover_depois(path, delay=120):
            import time; time.sleep(delay)
            try: os.remove(path)
            except: pass

        threading.Thread(target=remover_depois, args=(video_out,), daemon=True).start()

        resp = send_file(video_out, mimetype='video/mp4',
                         as_attachment=True, download_name='lipsync_final.mp4')
        resp.headers['X-Texto-Gerado']           = texto
        resp.headers['Access-Control-Expose-Headers'] = 'X-Texto-Gerado'
        return resp

    except Exception as e:
        print(f'[LipSync] ERRO: {e}')
        return jsonify({'erro': str(e)}), 500
    finally:
        for p in [video_in, audio_path]:
            try:
                if os.path.exists(p): os.remove(p)
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
