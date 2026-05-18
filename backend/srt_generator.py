"""Generate SRT and WebVTT subtitle files from Whisper/Groq transcript segments."""
import os


def _fmt(seconds: float, dot: bool = False) -> str:
    """
    Convert float seconds to timestamp.
    SRT:  HH:MM:SS,mmm  (dot=False)
    VTT:  HH:MM:SS.mmm  (dot=True)
    """
    s   = max(0.0, seconds)
    h   = int(s // 3600)
    m   = int((s % 3600) // 60)
    sec = int(s % 60)
    ms  = int(round((s % 1) * 1000))
    sep = '.' if dot else ','
    return f'{h:02d}:{m:02d}:{sec:02d}{sep}{ms:03d}'


def segments_to_vtt(segments: list, offset: float = 0.0) -> str:
    """Convert segments to WebVTT string (for HTML5 <track> element)."""
    lines = ['WEBVTT', '']
    for i, seg in enumerate(segments, 1):
        text = (seg.get('text') or '').strip()
        if not text:
            continue
        s = max(0.0, seg['start'] - offset)
        e = max(s + 0.1, seg['end']   - offset)
        lines.append(str(i))
        lines.append(f'{_fmt(s, dot=True)} --> {_fmt(e, dot=True)}')
        lines.append(text)
        lines.append('')
    return '\n'.join(lines)


def write_vtt(segments: list, path: str, offset: float = 0.0) -> str:
    """Write WebVTT file from segments. Returns path."""
    content = segments_to_vtt(segments, offset)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def segments_to_srt(segments: list, offset: float = 0.0) -> str:
    """
    Convert list of {start, end, text} dicts to SRT string.
    `offset` is subtracted from timestamps (for clips that start mid-video).
    """
    lines = []
    idx   = 1
    for seg in segments:
        text = (seg.get('text') or '').strip()
        if not text:
            continue
        s = max(0.0, seg['start'] - offset)
        e = max(s + 0.1, seg['end']   - offset)
        lines.append(f'{idx}\n{_fmt(s)} --> {_fmt(e)}\n{text}\n')
        idx += 1
    return '\n'.join(lines)


def write_srt(segments: list, path: str, offset: float = 0.0) -> str:
    """Write SRT file from segments. Returns path."""
    content = segments_to_srt(segments, offset)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def write_both(segments: list, base_path: str, offset: float = 0.0) -> dict:
    """Write both .srt and .vtt files. Returns {srt, vtt} paths."""
    srt = base_path if base_path.endswith('.srt') else base_path + '.srt'
    vtt = srt.replace('.srt', '.vtt')
    write_srt(segments, srt, offset)
    write_vtt(segments, vtt, offset)
    return {'srt': srt, 'vtt': vtt}


def split_long_segments(segments: list, max_chars: int = 60) -> list:
    """
    Split segments whose text is longer than max_chars into smaller pieces.
    Useful for word-wrap on short video screens.
    """
    out = []
    for seg in segments:
        text = (seg.get('text') or '').strip()
        dur  = seg['end'] - seg['start']

        if len(text) <= max_chars:
            out.append(seg)
            continue

        # Split into chunks of max_chars words
        words  = text.split()
        chunks = []
        cur    = []
        for w in words:
            cur.append(w)
            if len(' '.join(cur)) >= max_chars:
                chunks.append(' '.join(cur))
                cur = []
        if cur:
            chunks.append(' '.join(cur))

        # Distribute time evenly across chunks
        chunk_dur = dur / len(chunks)
        for i, chunk in enumerate(chunks):
            out.append({
                'start': seg['start'] + i * chunk_dur,
                'end':   seg['start'] + (i + 1) * chunk_dur,
                'text':  chunk,
            })
    return out


def clip_segments(segments: list, start: float, end: float) -> list:
    """Return only segments that overlap the [start, end] window."""
    result = []
    for seg in segments:
        if seg['end'] <= start or seg['start'] >= end:
            continue
        result.append({
            'start': max(seg['start'], start),
            'end':   min(seg['end'],   end),
            'text':  seg['text'],
        })
    return result
