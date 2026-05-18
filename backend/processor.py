"""Video processing — FFmpeg based (fast, battle-tested)."""
import os, uuid, subprocess, json

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── FFmpeg helpers ────────────────────────────────────────────────────

def _ffmpeg(*args, check=True):
    """Run ffmpeg; raises on error and always prints stderr for debugging."""
    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'warning'] + list(args)
    print(f'[FFmpeg] {" ".join(str(a) for a in args[:6])}…')
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr.strip():
        for line in result.stderr.strip().splitlines():
            print(f'[FFmpeg WARN] {line}')
    if check and result.returncode != 0:
        raise RuntimeError(f'FFmpeg error (code {result.returncode}):\n{result.stderr[-1200:]}')
    print(f'[FFmpeg] OK (exit {result.returncode})')
    return result


def _ffprobe(path):
    """Return dict with duration, width, height, fps."""
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_streams', '-show_format', path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(r.stdout)
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), {})
    fmt   = data.get('format', {})

    fps_raw = video.get('r_frame_rate', '30/1')
    try:
        num, den = fps_raw.split('/')
        fps = float(num) / float(den)
    except Exception:
        fps = 30.0

    return {
        'duration': float(fmt.get('duration', 0) or video.get('duration', 0) or 0),
        'width':    int(video.get('width',  1920)),
        'height':   int(video.get('height', 1080)),
        'fps':      round(fps, 2),
        'has_audio': any(s.get('codec_type') == 'audio' for s in data.get('streams', [])),
    }


# ── Public API ────────────────────────────────────────────────────────

def get_video_info(path: str) -> dict:
    return _ffprobe(path)


def extract_audio(video_path: str) -> str:
    """Extract audio to .wav for Whisper."""
    out = os.path.join(OUTPUT_DIR, f'audio_{uuid.uuid4().hex[:8]}.wav')
    _ffmpeg(
        '-i', video_path,
        '-vn',                    # no video
        '-ar', '16000',           # 16 kHz (Whisper preferred)
        '-ac', '1',               # mono
        '-c:a', 'pcm_s16le',      # uncompressed WAV
        out
    )
    return out


def cut_clip_9_16(video_path: str, start: float, end: float,
                  output_path: str = None) -> str:
    """
    Cut start→end seconds and export as 9:16 (1080×1920) MP4.
    No subtitles — clean clip.
    """
    if not output_path:
        output_path = os.path.join(OUTPUT_DIR, f'clip_{uuid.uuid4().hex[:8]}.mp4')

    info = _ffprobe(video_path)
    w, h = info['width'], info['height']

    # Build crop filter: center-crop to 9:16
    if (w / h) > (9 / 16):
        # landscape → crop sides
        crop_w = int(h * 9 / 16)
        crop_h = h
        crop_x = (w - crop_w) // 2
        crop_y = 0
    else:
        # portrait already (or square) → crop top/bottom
        crop_w = w
        crop_h = int(w * 16 / 9)
        crop_x = 0
        crop_y = (h - crop_h) // 2

    vf = f'crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=1080:1920:flags=lanczos'

    dur = max(0.5, end - start)
    _ffmpeg(
        '-ss', str(start),
        '-t',  str(dur),          # use duration, not end-time (avoids 1s bug)
        '-i',  video_path,
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
        '-c:a', 'aac',     '-b:a',    '128k',
        '-movflags', '+faststart',
        output_path
    )
    return output_path


def cut_clip_with_subtitles(video_path: str, start: float, end: float,
                             srt_path: str, output_path: str = None,
                             style: str = 'white', font_size: int = 22) -> str:
    """
    Cut start→end, crop to 9:16, and BURN subtitles into the video.
    style: white | yellow | box | pink | green | cyan | fire
    font_size: pixel size for the subtitle text (16=small, 22=medium, 30=large)
    """
    if not output_path:
        output_path = os.path.join(OUTPUT_DIR, f'short_{uuid.uuid4().hex[:8]}.mp4')

    info = _ffprobe(video_path)
    w, h = info['width'], info['height']

    if (w / h) > (9 / 16):
        crop_w = int(h * 9 / 16)
        crop_h = h
        crop_x = (w - crop_w) // 2
        crop_y = 0
    else:
        crop_w = w
        crop_h = int(w * 16 / 9)
        crop_x = 0
        crop_y = (h - crop_h) // 2

    # ASS color format: &HAABBGGRR  (A=Alpha 00=opaque, then Blue, Green, Red)
    # Yellow=&H0000FFFF  Pink=&H00B469FF  Green=&H0000FF00  Cyan=&H00FFFF00  Orange=&H0000A5FF
    fs = int(font_size) if font_size else 22
    styles = {
        'white':  f"FontName=Arial,Bold=1,FontSize={fs},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=2,Shadow=1,Alignment=2,MarginV=40",
        'yellow': f"FontName=Arial,Bold=1,FontSize={fs},PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=2,Shadow=1,Alignment=2,MarginV=40",
        'box':    f"FontName=Arial,Bold=1,FontSize={fs},PrimaryColour=&H00FFFFFF,BackColour=&H00000000,BorderStyle=4,Outline=0,Shadow=0,Alignment=2,MarginV=40",
        'pink':   f"FontName=Arial,Bold=1,FontSize={fs},PrimaryColour=&H00B469FF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=2,Shadow=1,Alignment=2,MarginV=40",
        'green':  f"FontName=Arial,Bold=1,FontSize={fs},PrimaryColour=&H0000FF00,OutlineColour=&H00000000,BackColour=&H00000000,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=40",
        'cyan':   f"FontName=Arial,Bold=1,FontSize={fs},PrimaryColour=&H00FFFF00,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=2,Shadow=1,Alignment=2,MarginV=40",
        'fire':   f"FontName=Arial,Bold=1,FontSize={fs},PrimaryColour=&H0000A5FF,OutlineColour=&H000000FF,BackColour=&H80000000,BorderStyle=3,Outline=2,Shadow=1,Alignment=2,MarginV=40",
    }
    force_style = styles.get(style, styles['white'])
    print(f'[FFmpeg] Queimando legenda: style={style} fs={fs}px | {os.path.basename(srt_path)}')

    # Escape SRT path for FFmpeg subtitles filter
    # On Windows: C:\path -> C\:/path  (colon after drive letter must be escaped)
    # On Linux/Mac: just forward slashes, no colon issue
    srt_escaped = srt_path.replace('\\', '/')
    # escape colon only if it's a Windows drive letter (e.g. C:/)
    import re as _re
    srt_escaped = _re.sub(r'^([A-Za-z]):', r'\1\\:', srt_escaped)
    # escape spaces and single quotes
    srt_escaped = srt_escaped.replace("'", "\\'").replace(' ', '\\ ')

    vf = (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale=1080:1920:flags=lanczos,"
        f"subtitles='{srt_escaped}':force_style='{force_style}'"
    )

    dur = max(0.5, end - start)
    _ffmpeg(
        '-ss', str(start),
        '-t',  str(dur),
        '-i',  video_path,
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
        '-c:a', 'aac',     '-b:a',    '128k',
        '-movflags', '+faststart',
        output_path
    )
    return output_path
