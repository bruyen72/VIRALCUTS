#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_video.py
=============
Corrige áudio desincronizado em vídeos usando TTS para cada personagem.
Detecta gaps de silêncio no áudio original e alinha cada fala TTS
exatamente no ponto correto do vídeo.

Uso:
    python sync_video.py --video input.mp4 --script script.txt --output synced.mp4
    python sync_video.py --video input.mp4 --script falas.json --output out.mp4 --tts edge
    python sync_video.py --video input.mp4 --script script.txt --output out.mp4 --preview 5
    python sync_video.py --video input.mp4 --script script.txt --output out.mp4 --bgm music.mp3

Formatos de script: TXT, JSON, CSV, YAML
Motores TTS: edge (padrão, alta qualidade), gtts (Google)

Dependências:
    pip install pydub tqdm edge-tts gtts pyyaml
    ffmpeg deve estar instalado no sistema (PATH)
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from pydub import AudioSegment
    from pydub.silence import detect_nonsilent
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync_video")


# ── Dataclasses ────────────────────────────────────────────────────────────────
@dataclass
class SpeechLine:
    character: str
    text:      str
    start:     Optional[float] = None   # seg; None = auto-detectar
    end:       Optional[float] = None
    voice:     Optional[str]   = None


# ── FFmpeg helpers ─────────────────────────────────────────────────────────────
def _run(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    log.debug("CMD: %s", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"Falhou [{cmd[0]}]:\n{r.stderr[-500:]}")
    return r


def _duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def extract_audio(video: str, out_wav: str) -> None:
    """Extrai áudio do vídeo como WAV 16 kHz mono."""
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error",
          "-i", video, "-vn", "-ac", "1", "-ar", "16000",
          "-acodec", "pcm_s16le", "-y", out_wav])


def detect_speech_intervals(
    wav: str,
    silence_thresh: int = -38,
    min_silence_ms: int = 300,
    min_speech_ms:  int = 200,
) -> List[Tuple[float, float]]:
    """
    Retorna lista de (start_s, end_s) onde há fala no áudio.
    Usa pydub se disponível, senão usa ffmpeg silencedetect.
    """
    if HAS_PYDUB:
        audio = AudioSegment.from_wav(wav)
        chunks = detect_nonsilent(
            audio,
            min_silence_len=min_silence_ms,
            silence_thresh=silence_thresh,
        )
        return [(s / 1000.0, e / 1000.0) for s, e in chunks if (e - s) >= min_speech_ms]
    else:
        # Fallback: ffmpeg silencedetect
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", wav,
             "-af", f"silencedetect=noise={silence_thresh}dB:d={min_silence_ms/1000:.2f}",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
        out = r.stderr
        starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", out)]
        ends   = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", out)]
        total  = _duration(wav)
        silences = list(zip(starts, ends))
        intervals, cursor = [], 0.0
        for s, e in silences:
            if s - cursor >= min_speech_ms / 1000:
                intervals.append((cursor, s))
            cursor = e
        if total - cursor >= min_speech_ms / 1000:
            intervals.append((cursor, total))
        return intervals


# ── TTS engines ────────────────────────────────────────────────────────────────
def _tts_edge(text: str, out_mp3: str, voice: str = "pt-BR-FranciscaNeural") -> None:
    import edge_tts

    async def _go():
        await edge_tts.Communicate(text, voice=voice).save(out_mp3)

    asyncio.run(_go())


def _tts_gtts(text: str, out_mp3: str, lang: str = "pt") -> None:
    from gtts import gTTS
    gTTS(text=text, lang=lang).save(out_mp3)


EDGE_VOICES = {
    "pt": "pt-BR-FranciscaNeural",
    "en": "en-US-JennyNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
}


def generate_tts(text: str, out_mp3: str, engine: str, lang: str,
                 voice: Optional[str] = None) -> None:
    """Gera áudio TTS e salva em out_mp3."""
    v = voice or EDGE_VOICES.get(lang, "pt-BR-FranciscaNeural")
    if engine == "edge":
        try:
            _tts_edge(text, out_mp3, voice=v)
            return
        except Exception as exc:
            log.warning("edge-tts falhou (%s), tentando gTTS...", exc)
    _tts_gtts(text, out_mp3, lang=lang)


# ── Ajuste de duração ──────────────────────────────────────────────────────────
def stretch_to_fit(src: str, dst: str, target_s: float) -> None:
    """
    Ajusta a duração do áudio para target_s via atempo.
    Encadeia filtros se o ratio estiver fora de [0.5, 2.0].
    """
    src_dur = _duration(src)
    if src_dur <= 0:
        shutil.copy(src, dst)
        return
    ratio = src_dur / max(target_s, 0.1)
    ratio = max(0.5, min(ratio, 4.0))

    filters, r = [], ratio
    while r > 2.0:
        filters.append("atempo=2.0"); r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5"); r *= 2.0
    filters.append(f"atempo={r:.5f}")

    _run(["ffmpeg", "-hide_banner", "-loglevel", "error",
          "-i", src, "-af", ",".join(filters), "-y", dst])


# ── Parser de scripts ──────────────────────────────────────────────────────────
def parse_script(path: str) -> List[SpeechLine]:
    ext = Path(path).suffix.lower()
    if ext == ".json":
        return _parse_json(path)
    if ext == ".csv":
        return _parse_csv(path)
    if ext in (".yaml", ".yml"):
        return _parse_yaml(path)
    return _parse_txt(path)


def _parse_json(path: str) -> List[SpeechLine]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    lines = []
    # {"Peixe": "texto", ...}  ou  [{"character":..., "text":..., "start":...}, ...]
    if isinstance(data, dict):
        for char, val in data.items():
            if isinstance(val, str):
                lines.append(SpeechLine(character=char, text=val))
            elif isinstance(val, dict):
                lines.append(SpeechLine(
                    character=char,
                    text=val.get("text", val.get("texto", "")),
                    start=val.get("start"), end=val.get("end"),
                    voice=val.get("voice"),
                ))
    elif isinstance(data, list):
        for item in data:
            lines.append(SpeechLine(
                character=item.get("character", item.get("personagem", "Narrador")),
                text=item.get("text", item.get("texto", "")),
                start=item.get("start"), end=item.get("end"),
                voice=item.get("voice"),
            ))
    return lines


def _parse_csv(path: str) -> List[SpeechLine]:
    lines = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            lines.append(SpeechLine(
                character=row.get("character", row.get("personagem", "Narrador")),
                text=row.get("text", row.get("texto", "")),
                start=float(row["start"]) if row.get("start") else None,
                end=float(row["end"])     if row.get("end")   else None,
            ))
    return lines


def _parse_yaml(path: str) -> List[SpeechLine]:
    try:
        import yaml
    except ImportError:
        raise ImportError("Instale pyyaml: pip install pyyaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return [SpeechLine(
            character=i.get("character", "Narrador"),
            text=i.get("text", ""),
            start=i.get("start"), end=i.get("end"),
        ) for i in data]
    if isinstance(data, dict):
        return [SpeechLine(character=k, text=v)
                for k, v in data.items() if isinstance(v, str)]
    return []


def _parse_txt(path: str) -> List[SpeechLine]:
    """
    Aceita:
        Peixe: Oi, tudo bem?
        Humano: Olá!
    ou linhas simples sem prefixo (character = Narrador).
    """
    lines = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            if ":" in raw:
                char, _, text = raw.partition(":")
                lines.append(SpeechLine(character=char.strip(), text=text.strip()))
            else:
                lines.append(SpeechLine(character="Narrador", text=raw))
    return lines


# ── Núcleo de sincronização ────────────────────────────────────────────────────
def sync_video(
    video:      str,
    script:     str,
    output:     str,
    tts_engine: str   = "edge",
    lang:       str   = "pt",
    bgm:        Optional[str] = None,
    bgm_vol:    float = 0.15,
    preview_s:  int   = 0,
    stretch:    bool  = True,
    silence_db: float = -38.0,
    silence_dur:float = 0.3,
    log_path:   Optional[str] = None,
) -> str:

    if not os.path.exists(video):
        raise FileNotFoundError(f"Vídeo não encontrado: {video}")
    if not os.path.exists(script):
        raise FileNotFoundError(f"Script não encontrado: {script}")

    tmp = tempfile.mkdtemp(prefix="syncvid_")
    log_entries = []

    try:
        # ── 1. Parse do script ───────────────────────────────────────
        lines = parse_script(script)
        if not lines:
            raise ValueError("Script vazio ou inválido.")
        log.info("Script: %d falas carregadas", len(lines))

        # ── 2. Extrair áudio original ────────────────────────────────
        log.info("Extraindo áudio do vídeo...")
        wav_orig = os.path.join(tmp, "original.wav")
        if preview_s > 0:
            _run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                  "-i", video, "-t", str(preview_s),
                  "-vn", "-ac", "1", "-ar", "16000",
                  "-acodec", "pcm_s16le", "-y", wav_orig])
        else:
            extract_audio(video, wav_orig)

        vid_dur = _duration(video)
        if preview_s > 0:
            vid_dur = min(vid_dur, float(preview_s))

        # ── 3. Detectar intervalos de fala ───────────────────────────
        has_timestamps = any(l.start is not None for l in lines)

        if has_timestamps:
            intervals = [(l.start, l.end or (l.start + 5.0)) for l in lines]
            log.info("Usando timestamps do script.")
        else:
            log.info("Detectando silêncios no áudio original...")
            intervals = detect_speech_intervals(
                wav_orig,
                silence_thresh=int(silence_db),
                min_silence_ms=int(silence_dur * 1000),
            )
            log.info("Intervalos detectados: %d", len(intervals))

            if len(intervals) != len(lines):
                log.warning(
                    "Intervalos (%d) ≠ falas (%d) → distribuição uniforme.",
                    len(intervals), len(lines),
                )
                step = vid_dur / len(lines)
                intervals = [(i * step, (i+1) * step) for i in range(len(lines))]

        # ── 4. Gerar TTS + alinhar cada fala ─────────────────────────
        audio_inputs, delay_filters, mix_labels = [], [], []

        iterator = enumerate(zip(lines, intervals))
        if HAS_TQDM:
            iterator = enumerate(tqdm(list(zip(lines, intervals)), desc="Gerando TTS"))

        for idx, (line, (t_start, t_end)) in iterator:
            target_dur = max(0.3, t_end - t_start)
            log.info(
                "[%d/%d] %s → '%s' (%.2fs–%.2fs, alvo=%.2fs)",
                idx+1, len(lines), line.character, line.text[:40],
                t_start, t_end, target_dur,
            )

            # Gerar TTS
            raw_mp3  = os.path.join(tmp, f"tts_{idx:03d}_raw.mp3")
            final_mp3 = os.path.join(tmp, f"tts_{idx:03d}.mp3")
            try:
                generate_tts(line.text, raw_mp3, tts_engine, lang, line.voice)
            except Exception as exc:
                raise RuntimeError(f"TTS falhou para '{line.character}': {exc}")

            tts_dur = _duration(raw_mp3)
            log.info("  TTS: %.2fs gerado (alvo: %.2fs)", tts_dur, target_dur)

            # Time-stretch se necessário
            if stretch and abs(tts_dur - target_dur) > 0.05:
                try:
                    stretch_to_fit(raw_mp3, final_mp3, target_dur)
                    log.info("  Ajustado para %.2fs", _duration(final_mp3))
                except Exception as exc:
                    log.warning("  Stretch falhou (%s), usando sem ajuste.", exc)
                    shutil.copy(raw_mp3, final_mp3)
            else:
                shutil.copy(raw_mp3, final_mp3)

            log_entries.append({
                "index": idx,
                "character": line.character,
                "text": line.text[:80],
                "target_start_s": round(t_start, 3),
                "target_end_s":   round(t_end, 3),
                "tts_raw_s":      round(tts_dur, 3),
                "tts_final_s":    round(_duration(final_mp3), 3),
            })

            # Posicionar com adelay (em ms)
            delay_ms = int(t_start * 1000)
            audio_inputs.extend(["-i", final_mp3])
            delay_filters.append(
                f"[{idx+1}:a]adelay={delay_ms}|{delay_ms}[a{idx}]"
            )
            mix_labels.append(f"[a{idx}]")

        # ── 5. Montar filter_complex ──────────────────────────────────
        n = len(lines)
        fc_parts = delay_filters[:]
        fc_parts.append(
            f"{''.join(mix_labels)}amix=inputs={n}:duration=longest:normalize=0[tts_out]"
        )
        final_label = "[tts_out]"

        bgm_inputs = []
        if bgm and os.path.exists(bgm):
            bgm_idx = n + 1
            bgm_inputs = ["-i", bgm]
            fc_parts.append(f"[{bgm_idx}:a]volume={bgm_vol:.3f}[bgm_v]")
            fc_parts.append("[tts_out][bgm_v]amix=inputs=2:duration=first:normalize=0[final_a]")
            final_label = "[final_a]"
            log.info("Música de fundo adicionada (volume=%.0f%%)", bgm_vol * 100)

        filter_complex = ";".join(fc_parts)

        # ── 6. Exportar vídeo final ───────────────────────────────────
        log.info("Exportando vídeo final...")
        preview_args = (["-t", str(preview_s)] if preview_s > 0 else [])

        tmp_out = os.path.join(tmp, "output.mp4")
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", video,
            *audio_inputs,
            *bgm_inputs,
            *preview_args,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", final_label,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-async", "1",
            "-y", tmp_out,
        ], check=True)

        # Mover para destino
        if os.path.exists(output):
            os.remove(output)
        shutil.move(tmp_out, output)
        log.info("✅ Vídeo salvo: %s", output)

        # ── 7. Salvar log ─────────────────────────────────────────────
        if log_path:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_entries, f, ensure_ascii=False, indent=2)
            log.info("Log salvo: %s", log_path)

        return output

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── CLI ────────────────────────────────────────────────────────────────────────
def _check_deps():
    # ffmpeg obrigatório
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        log.error("FFmpeg não encontrado. Baixe em https://ffmpeg.org")
        sys.exit(1)

    if not HAS_PYDUB:
        log.warning("pydub não instalado (pip install pydub). Usando fallback ffmpeg para detecção de silêncio.")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sync_video.py",
        description="Sincroniza áudio TTS com vídeo para qualquer personagem.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Formatos de script suportados:
  TXT  ->  Peixe: Oi, tudo bem?
  JSON ->  [{"character":"Peixe","text":"Oi!","start":0.0,"end":3.0}]
  CSV  ->  character,text,start,end
  YAML ->  - character: Peixe / text: Oi!

Exemplos:
  python sync_video.py --video peixe.mp4 --script falas.txt --output final.mp4
  python sync_video.py --video v.mp4 --script f.json --output out.mp4 --tts gtts
  python sync_video.py --video v.mp4 --script f.txt  --output out.mp4 --preview 5
  python sync_video.py --video v.mp4 --script f.json --output out.mp4 --bgm music.mp3 --log timing.json
        """,
    )
    p.add_argument("--video",        required=True,           help="Vídeo de entrada")
    p.add_argument("--script",       required=True,           help="Script de falas (.txt/.json/.csv/.yaml)")
    p.add_argument("--output",       required=True,           help="Vídeo de saída")
    p.add_argument("--tts",          default="edge",          choices=["edge","gtts"], help="Motor TTS (padrão: edge)")
    p.add_argument("--lang",         default="pt",            help="Idioma TTS (padrão: pt)")
    p.add_argument("--bgm",          default=None,            help="Música de fundo (opcional)")
    p.add_argument("--bgm-vol",      default=0.15, type=float,help="Volume da música 0-1 (padrão: 0.15)")
    p.add_argument("--preview",      default=0,    type=int,  help="Processar apenas N segundos (0 = tudo)")
    p.add_argument("--no-stretch",   action="store_true",     help="Desativar ajuste de duração do TTS")
    p.add_argument("--silence-db",   default=-38.0,type=float,help="Limiar de silêncio em dB (padrão: -38)")
    p.add_argument("--silence-dur",  default=0.3,  type=float,help="Duração mínima do silêncio em s (padrão: 0.3)")
    p.add_argument("--log",          default=None,            help="Salvar log de timing em JSON")
    p.add_argument("--verbose",      action="store_true",     help="Modo verbose")
    return p


def main():
    parser = _build_parser()
    args   = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    _check_deps()

    t0 = time.time()
    try:
        out = sync_video(
            video      = args.video,
            script     = args.script,
            output     = args.output,
            tts_engine = args.tts,
            lang       = args.lang,
            bgm        = args.bgm,
            bgm_vol    = args.bgm_vol,
            preview_s  = args.preview,
            stretch    = not args.no_stretch,
            silence_db = args.silence_db,
            silence_dur= args.silence_dur,
            log_path   = args.log,
        )
        log.info("Concluído em %.1fs → %s", time.time() - t0, out)
    except FileNotFoundError as e:
        log.error("%s", e); sys.exit(2)
    except RuntimeError as e:
        log.error("%s", e); sys.exit(3)
    except KeyboardInterrupt:
        log.info("Interrompido."); sys.exit(0)


if __name__ == "__main__":
    main()
