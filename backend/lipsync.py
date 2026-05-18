"""
Lip-sync Opção C — funciona em qualquer vídeo (peixe, animal, pessoa).
Usa amplitude do áudio frame a frame para animar a região da boca.
Depende de: opencv-python-headless, librosa, numpy
"""
import os
import subprocess
import tempfile
import numpy as np

try:
    import cv2
    import librosa
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def verificar_deps():
    if not HAS_DEPS:
        raise RuntimeError(
            'Dependências faltando. Execute no servidor:\n'
            'pip install opencv-python-headless librosa numpy'
        )


def analisar_amplitude(audio_path: str, fps: float = 30.0) -> list:
    """
    Analisa o áudio e retorna lista de amplitudes (0.0–1.0) por frame.
    """
    verificar_deps()
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    hop = max(1, int(sr / fps))
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    if rms.max() > 0:
        rms = rms / rms.max()
    # suavizar com janela deslizante para movimento mais natural
    kernel = np.ones(3) / 3
    rms = np.convolve(rms, kernel, mode='same')
    return rms.tolist()


def aplicar_lipsync(
    video_path: str,
    audio_path: str,
    boca: dict,
    output_path: str,
    intensidade: float = 0.4,
) -> str:
    """
    Aplica o efeito lip-sync no vídeo.

    boca = {
        'x': int,         # posição X da boca no PREVIEW
        'y': int,         # posição Y da boca no PREVIEW
        'w': int,         # largura da boca no PREVIEW
        'h': int,         # altura da boca no PREVIEW
        'preview_w': int, # largura do elemento <video> no browser
        'preview_h': int, # altura do elemento <video> no browser
    }
    """
    verificar_deps()

    # ── Abrir vídeo ───────────────────────────────────────────────────
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f'Não foi possível abrir o vídeo: {video_path}')

    vid_w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vid_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # ── Escalar coordenadas do preview para o vídeo real ─────────────
    pw = boca.get('preview_w') or vid_w
    ph = boca.get('preview_h') or vid_h
    sx = vid_w / pw
    sy = vid_h / ph

    bx = max(0, int(boca['x'] * sx))
    by = max(0, int(boca['y'] * sy))
    bw = max(4, int(boca['w'] * sx))
    bh = max(4, int(boca['h'] * sy))

    print(f'[LipSync] vídeo={vid_w}x{vid_h} fps={vid_fps:.1f}')
    print(f'[LipSync] boca no vídeo: x={bx} y={by} w={bw} h={bh}')

    # ── Analisar amplitude do áudio ───────────────────────────────────
    amplitudes = analisar_amplitude(audio_path, fps=vid_fps)

    # ── Processar frames ──────────────────────────────────────────────
    tmp_video = tempfile.mktemp(suffix='_ls_noaudio.mp4')
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = cv2.VideoWriter(tmp_video, fourcc, vid_fps, (vid_w, vid_h))

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        amp = float(amplitudes[min(frame_idx, len(amplitudes) - 1)])

        if amp > 0.08:  # só anima quando tem voz detectável
            escala_h = 1.0 + amp * intensidade   # abre a boca (aumenta altura)
            escala_w = 1.0 + amp * (intensidade * 0.3)  # leve alargamento

            # Dimensões originais da região da boca
            y1 = max(0, by)
            y2 = min(vid_h, by + bh)
            x1 = max(0, bx)
            x2 = min(vid_w, bx + bw)

            regiao = frame[y1:y2, x1:x2]
            if regiao.size == 0:
                writer.write(frame)
                frame_idx += 1
                continue

            novo_h = max(1, int((y2 - y1) * escala_h))
            novo_w = max(1, int((x2 - x1) * escala_w))

            # Centrar a nova região na posição original da boca
            centro_y = (y1 + y2) // 2
            centro_x = (x1 + x2) // 2
            ny1 = max(0, centro_y - novo_h // 2)
            ny2 = min(vid_h, ny1 + novo_h)
            nx1 = max(0, centro_x - novo_w // 2)
            nx2 = min(vid_w, nx1 + novo_w)

            # Redimensionar e colar
            try:
                scaled = cv2.resize(regiao, (nx2 - nx1, ny2 - ny1),
                                    interpolation=cv2.INTER_LINEAR)
                frame[ny1:ny2, nx1:nx2] = scaled
            except Exception:
                pass  # se falhar no frame, pula silenciosamente

        writer.write(frame)
        frame_idx += 1

    cap.release()
    writer.release()
    print(f'[LipSync] {frame_idx} frames processados')

    # ── Combinar vídeo processado + áudio TTS ─────────────────────────
    cmd = [
        'ffmpeg', '-y',
        '-i', tmp_video,
        '-i', audio_path,
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest',
        '-async', '1',
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    try:
        os.remove(tmp_video)
    except Exception:
        pass

    if res.returncode != 0:
        raise RuntimeError(f'ffmpeg lipsync falhou:\n{res.stderr[-600:]}')

    return output_path
