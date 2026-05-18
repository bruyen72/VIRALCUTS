#!/usr/bin/env python3
"""
sync_video.py — Sincroniza áudio TTS com vídeo para qualquer personagem.

Uso:
    python sync_video.py --video input.mp4 --script script.json --output out.mp4
    python sync_video.py --video input.mp4 --script script.txt  --output out.mp4 --tts edge
    python sync_video.py --video input.mp4 --script script.json --output out.mp4 --preview
    python sync_video.py --video input.mp4 --script script.json --output out.mp4 --bgm music.mp3

Formatos de script suportados: JSON, TXT, CSV, YAML
Motores TTS suportados:  edge (edge-tts, padrão), gtts (gTTS), auto
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync_video")


# ─── Dataclasses ──────────────────────────────────────────────────────────────
@dataclass
class SpeechEntry:
    """Uma linha de fala do script."""
    character: str
    text: str
    start: Optional[float] = None   # segundos; None = detectar automaticamente
    end:   Optional[float] = None
    voice: Optional[str]   = None   # voz TTS específica para este personagem


@dataclass
class SyncConfig:
    video_path:  str
    script_path: str
    output_path: str
    tts_engine:  str  = "edge"     # edge | gtts | auto
    language:    str  = "pt"
    bgm_path:    Optional[str] = None
    bgm_volume:  float = 0.15       # volume da música de fundo (0-1)
    preview_sec: int   = 0          # 0 = processar tudo; >0 = apenas N segundos
    stretch:     bool  = True       # time-stretch TTS para caber no intervalo
    log_path:    Optional[str] = None
    silence_db:  float = -35.0      # threshold de silêncio (dB)
    silence_dur: float = 0.3        # duração mínima do silêncio (s)
    overwrite:   bool  = True


# ─── Utilitários FFmpeg ───────────────────────────────────────────────────────
def ffmpeg(*args, check=True, quiet=True) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-hide_banner", *([] if not quiet else ["-loglevel", "error"]), *args]
    log.debug("FFmpeg: %s", " ".join(str(a) for a in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"FFmpeg falhou:\n{result.stderr[-600:]}")
    return result


def ffprobe_duration(path: str) -> float:
    """Retorna duração do arquivo em segundos."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def detect_silence(audio_path: str, noise_db: float = -35.0, min_dur: float = 0.3) -> List[Tuple[float, float]]:
    """
    Usa ffmpeg silencedetect para retornar lista de (start, end) dos silêncios.
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", audio_path,
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
         "-f", "null", "-"],
        capture_output=True, text=True
    )
    output = r.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", output)]
    ends   = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", output)]
    return list(zip(starts, ends))


def silence_to_speech(silences: List[Tuple[float,float]], total_dur: float, min_speech: float = 0.3) -> List[Tuple[float, float]]:
    """Converte lista de silêncios em lista de intervalos de fala."""
    intervals = []
    cursor = 0.0
    for (s_start, s_end) in silences:
        if s_start - cursor >= min_speech:
            intervals.append((cursor, s_start))
        cursor = s_end
    if total_dur - cursor >= min_speech:
        intervals.append((cursor, total_dur))
    return intervals


# ─── Parser de scripts ────────────────────────────────────────────────────────
class ScriptParser:
    @staticmethod
    def parse(path: str) -> List[SpeechEntry]:
        ext = Path(path).suffix.lower()
        if ext == ".json":
            return ScriptParser._json(path)
        elif ext == ".csv":
            return ScriptParser._csv(path)
        elif ext in (".yaml", ".yml"):
            return ScriptParser._yaml(path)
        else:
            return ScriptParser._txt(path)

    @staticmethod
    def _json(path: str) -> List[SpeechEntry]:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        entries = []
        # Formato 1: {"Peixe": "texto", "Humano": "texto"}  (simples)
        if isinstance(data, dict) and all(isinstance(v, str) for v in data.values()):
            for char, text in data.items():
                entries.append(SpeechEntry(character=char, text=text))
            return entries

        # Formato 2: lista de objetos
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    entries.append(SpeechEntry(
                        character = item.get("character", item.get("personagem", "Narrador")),
                        text      = item.get("text", item.get("texto", "")),
                        start     = item.get("start"),
                        end       = item.get("end"),
                        voice     = item.get("voice"),
                    ))
            return entries

        # Formato 3: {"falas": [...]}
        if isinstance(data, dict) and "falas" in data:
            return ScriptParser._json.__func__(ScriptParser,
                _write_tmp(json.dumps(data["falas"])))

        raise ValueError(f"Formato JSON não reconhecido em {path}")

    @staticmethod
    def _csv(path: str) -> List[SpeechEntry]:
        entries = []
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                entries.append(SpeechEntry(
                    character = row.get("character", row.get("personagem", "Narrador")),
                    text      = row.get("text", row.get("texto", "")),
                    start     = float(row["start"]) if row.get("start") else None,
                    end       = float(row["end"])   if row.get("end")   else None,
                ))
        return entries

    @staticmethod
    def _yaml(path: str) -> List[SpeechEntry]:
        try:
            import yaml
        except ImportError:
            raise ImportError("Instale pyyaml: pip install pyyaml")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        entries = []
        if isinstance(data, list):
            for item in data:
                entries.append(SpeechEntry(
                    character = item.get("character", "Narrador"),
                    text      = item.get("text", ""),
                    start     = item.get("start"),
                    end       = item.get("end"),
                ))
        elif isinstance(data, dict):
            for char, text in data.items():
                if isinstance(text, str):
                    entries.append(SpeechEntry(character=char, text=text))
        return entries

    @staticmethod
    def _txt(path: str) -> List[SpeechEntry]:
        """
        Formato suportado:
          Peixe: Oi gente!
          Humano: Olá, mundo!
          Texto sem prefixo também funciona (personagem = Narrador)
        """
        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    char, _, text = line.partition(":")
                    entries.append(SpeechEntry(character=char.strip(), text=text.strip()))
                else:
                    entries.append(SpeechEntry(character="Narrador", text=line))
        return entries


def _write_tmp(content: str) -> str:
    tmp = tempfile.mktemp(suffix=".json")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    return tmp


# ─── Motores TTS ──────────────────────────────────────────────────────────────
class TTSEngine:
    def generate(self, text: str, output_path: str, voice: Optional[str] = None) -> str:
        raise NotImplementedError


class EdgeTTSEngine(TTSEngine):
    DEFAULT_VOICES = {
        "pt": "pt-BR-FranciscaNeural",
        "en": "en-US-JennyNeural",
        "es": "es-ES-ElviraNeural",
    }

    def __init__(self, lang: str = "pt"):
        self.lang = lang

    def generate(self, text: str, output_path: str, voice: Optional[str] = None) -> str:
        import edge_tts
        v = voice or self.DEFAULT_VOICES.get(self.lang, "pt-BR-FranciscaNeural")

        async def _run():
            comm = edge_tts.Communicate(text, voice=v)
            await comm.save(output_path)

        asyncio.run(_run())
        return output_path


class GTTSEngine(TTSEngine):
    def __init__(self, lang: str = "pt"):
        self.lang = lang

    def generate(self, text: str, output_path: str, voice: Optional[str] = None) -> str:
        from gtts import gTTS
        tts = gTTS(text=text, lang=self.lang)
        tts.save(output_path)
        return output_path


def make_tts_engine(name: str, lang: str) -> TTSEngine:
    if name == "edge":
        try:
            import edge_tts
            return EdgeTTSEngine(lang)
        except ImportError:
            log.warning("edge-tts não instalado, usando gTTS como fallback.")
            return GTTSEngine(lang)
    elif name == "gtts":
        return GTTSEngine(lang)
    elif name == "auto":
        try:
            import edge_tts
            return EdgeTTSEngine(lang)
        except ImportError:
            return GTTSEngine(lang)
    raise ValueError(f"Motor TTS desconhecido: {name}")


# ─── Ajuste de duração (time-stretch) ────────────────────────────────────────
def stretch_audio(input_path: str, output_path: str, target_duration: float) -> str:
    """
    Ajusta duração do áudio para target_duration usando atempo.
    Suporta qualquer taxa dentro de [0.5, 100] usando encadeamento de filtros.
    """
    src_dur = ffprobe_duration(input_path)
    if src_dur <= 0:
        raise RuntimeError(f"Não foi possível ler a duração de {input_path}")

    ratio = src_dur / target_duration          # >1 = precisamos acelerar
    ratio = max(0.5, min(ratio, 4.0))          # limitar à faixa razoável

    # atempo aceita 0.5–2.0; encadear se ratio > 2 ou < 0.5
    filters = []
    r = ratio
    while r > 2.0:
        filters.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5")
        r *= 2.0
    filters.append(f"atempo={r:.4f}")
    af = ",".join(filters)

    ffmpeg("-i", input_path, "-af", af, "-y", output_path)
    return output_path


def pad_audio(input_path: str, output_path: str, pad_start: float = 0.0, pad_end: float = 0.0) -> str:
    """Insere silêncio no início/fim do clip."""
    filters = []
    if pad_start > 0:
        filters.append(f"adelay={int(pad_start*1000)}|{int(pad_start*1000)}")
    if pad_end > 0:
        filters.append(f"apad=pad_dur={pad_end:.3f}")
    if not filters:
        import shutil; shutil.copy(input_path, output_path); return output_path
    ffmpeg("-i", input_path, "-af", ",".join(filters), "-y", output_path)
    return output_path


# ─── Núcleo de sincronização ──────────────────────────────────────────────────
class SyncEngine:
    def __init__(self, cfg: SyncConfig):
        self.cfg  = cfg
        self.tts  = make_tts_engine(cfg.tts_engine, cfg.language)
        self.tmp  = tempfile.mkdtemp(prefix="syncvideo_")
        self.logs : List[dict] = []

    def _tmp(self, name: str) -> str:
        return os.path.join(self.tmp, name)

    def run(self) -> str:
        cfg = self.cfg
        log.info("Iniciando sincronização: %s → %s", cfg.video_path, cfg.output_path)

        # 0 — Validações
        if not os.path.exists(cfg.video_path):
            raise FileNotFoundError(f"Vídeo não encontrado: {cfg.video_path}")
        if not os.path.exists(cfg.script_path):
            raise FileNotFoundError(f"Script não encontrado: {cfg.script_path}")

        # 1 — Parse do script
        entries = ScriptParser.parse(cfg.script_path)
        if not entries:
            raise ValueError("Script vazio ou inválido.")
        log.info("Script carregado: %d falas", len(entries))

        # 2 — Duração do vídeo
        vid_dur = ffprobe_duration(cfg.video_path)
        if cfg.preview_sec > 0:
            vid_dur = min(vid_dur, float(cfg.preview_sec))
            log.info("Modo preview: processando %.1fs", vid_dur)

        # 3 — Extrair áudio original para detecção de silêncio
        orig_audio = self._tmp("original_audio.wav")
        t0 = cfg.preview_sec
        preview_args = (["-t", str(cfg.preview_sec)] if cfg.preview_sec > 0 else [])
        ffmpeg("-i", cfg.video_path, *preview_args, "-vn", "-ac", "1", "-ar", "16000", "-y", orig_audio)

        # 4 — Determinar intervalos de fala
        has_timestamps = any(e.start is not None for e in entries)
        if has_timestamps:
            intervals = [(e.start, e.end or (e.start + 4.0)) for e in entries]
            log.info("Usando timestamps fornecidos pelo usuário.")
        else:
            log.info("Detectando intervalos de fala no áudio original...")
            silences  = detect_silence(orig_audio, cfg.silence_db, cfg.silence_dur)
            intervals = silence_to_speech(silences, vid_dur)
            log.info("Intervalos detectados: %d", len(intervals))

            # Se a contagem não bate, avisar e distribuir uniformemente
            if len(intervals) != len(entries):
                log.warning(
                    "Intervalos detectados (%d) ≠ falas no script (%d). "
                    "Distribuindo uniformemente.", len(intervals), len(entries)
                )
                step = vid_dur / len(entries)
                intervals = [(i * step, (i + 1) * step) for i in range(len(entries))]

        # 5 — Gerar TTS e posicionar cada clip
        audio_inputs  = []   # argumentos -i para ffmpeg
        delay_filters = []   # adelay para posicionamento
        mix_labels    = []

        for idx, (entry, (t_start, t_end)) in enumerate(zip(entries, intervals)):
            target_dur = max(0.3, t_end - t_start)
            log.info("[%d/%d] '%s' — '%.40s...' → %.2fs–%.2fs",
                     idx+1, len(entries), entry.character, entry.text, t_start, t_end)

            # Gerar TTS
            raw_tts = self._tmp(f"tts_{idx:03d}_raw.mp3")
            try:
                self.tts.generate(entry.text, raw_tts, voice=entry.voice)
            except Exception as exc:
                log.error("Erro no TTS para '%s': %s", entry.character, exc)
                raise

            tts_dur = ffprobe_duration(raw_tts)
            log.info("  TTS gerado: %.2fs (alvo: %.2fs)", tts_dur, target_dur)

            # Time-stretch se necessário
            final_tts = self._tmp(f"tts_{idx:03d}_final.mp3")
            if cfg.stretch and abs(tts_dur - target_dur) > 0.05:
                try:
                    stretch_audio(raw_tts, final_tts, target_dur)
                    log.info("  Ajustado para %.2fs", target_dur)
                except Exception as exc:
                    log.warning("  Stretch falhou (%s), usando sem ajuste.", exc)
                    import shutil; shutil.copy(raw_tts, final_tts)
            else:
                import shutil; shutil.copy(raw_tts, final_tts)

            # Registrar no log
            self.logs.append({
                "index": idx,
                "character": entry.character,
                "text": entry.text[:80],
                "target_start": round(t_start, 3),
                "target_end":   round(t_end, 3),
                "tts_raw_dur":  round(tts_dur, 3),
                "tts_final_dur": round(ffprobe_duration(final_tts), 3),
            })

            # Preparar para mistura
            delay_ms = int(t_start * 1000)
            audio_inputs.extend(["-i", final_tts])
            delay_filters.append(f"[{idx+1}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
            mix_labels.append(f"[a{idx}]")

        # 6 — Montar filter_complex e exportar
        log.info("Montando trilha de áudio final...")
        n_tts = len(entries)
        filter_parts = delay_filters[:]

        # Misturar todos os clips TTS
        tts_mix = f"{''.join(mix_labels)}amix=inputs={n_tts}:duration=longest:normalize=0[tts_mix]"
        filter_parts.append(tts_mix)

        # Música de fundo (opcional)
        bgm_inputs = []
        final_label = "[tts_mix]"
        bgm_idx = n_tts + 1  # índice no ffmpeg para o bgm

        if cfg.bgm_path and os.path.exists(cfg.bgm_path):
            bgm_inputs = ["-i", cfg.bgm_path]
            bgm_vol = f"[{bgm_idx}:a]volume={cfg.bgm_volume}[bgm]"
            bgm_mix = f"[tts_mix][bgm]amix=inputs=2:duration=first:normalize=0[final_a]"
            filter_parts.extend([bgm_vol, bgm_mix])
            final_label = "[final_a]"
            log.info("Música de fundo adicionada: volume=%.0f%%", cfg.bgm_volume * 100)

        filter_complex = ";".join(filter_parts)

        # Preview: cortar o vídeo de entrada se necessário
        input_args = []
        if cfg.preview_sec > 0:
            input_args = ["-t", str(cfg.preview_sec)]

        out_tmp = self._tmp("output_raw.mp4")
        ffmpeg(
            "-i", cfg.video_path,
            *audio_inputs,
            *bgm_inputs,
            *input_args,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", final_label,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-async", "1",
            "-y", out_tmp,
            quiet=False,
        )

        # 7 — Mover para destino final
        import shutil
        if cfg.overwrite and os.path.exists(cfg.output_path):
            os.remove(cfg.output_path)
        shutil.move(out_tmp, cfg.output_path)
        log.info("✅ Vídeo salvo: %s", cfg.output_path)

        # 8 — Arquivo de log
        if cfg.log_path:
            with open(cfg.log_path, "w", encoding="utf-8") as f:
                json.dump(self.logs, f, ensure_ascii=False, indent=2)
            log.info("Log salvo: %s", cfg.log_path)

        return cfg.output_path

    def cleanup(self):
        import shutil
        try:
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:
            pass


# ─── CLI ─────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sync_video.py",
        description="Sincroniza áudio TTS com vídeo para qualquer personagem.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python sync_video.py --video peixe.mp4 --script falas.json --output final.mp4
  python sync_video.py --video input.mp4 --script falas.txt --output out.mp4 --tts gtts --lang pt
  python sync_video.py --video input.mp4 --script falas.json --output out.mp4 --preview 5
  python sync_video.py --video input.mp4 --script falas.json --output out.mp4 --bgm musica.mp3

Formato JSON (com timestamps):
  [
    {"character": "Peixe",  "text": "Oi! Eu sou o peixinho!", "start": 0.0, "end": 3.5},
    {"character": "Humano", "text": "Olá, peixe!",            "start": 4.0, "end": 6.0}
  ]

Formato JSON (simples, auto-detecta intervalos):
  {"Peixe": "Oi! Eu sou o peixinho!", "Humano": "Olá, peixe!"}

Formato TXT:
  Peixe: Oi! Eu sou o peixinho!
  Humano: Olá, peixe!
        """,
    )
    p.add_argument("--video",   required=True,  help="Arquivo de vídeo de entrada (.mp4, .mkv, etc.)")
    p.add_argument("--script",  required=True,  help="Script de falas (.json, .txt, .csv, .yaml)")
    p.add_argument("--output",  required=True,  help="Arquivo de vídeo de saída")
    p.add_argument("--tts",     default="edge", choices=["edge","gtts","auto"], help="Motor TTS (padrão: edge)")
    p.add_argument("--lang",    default="pt",   help="Idioma para TTS (padrão: pt)")
    p.add_argument("--bgm",     default=None,   help="Arquivo de música de fundo (opcional)")
    p.add_argument("--bgm-vol", default=0.15,   type=float, help="Volume da música de fundo 0-1 (padrão: 0.15)")
    p.add_argument("--preview", default=0,      type=int,   help="Gerar preview de N segundos (0 = completo)")
    p.add_argument("--no-stretch", action="store_true",     help="Desativar time-stretch do TTS")
    p.add_argument("--log",     default=None,   help="Salvar log de sincronização em arquivo JSON")
    p.add_argument("--silence-db",  default=-35.0, type=float, help="Threshold de silêncio em dB (padrão: -35)")
    p.add_argument("--silence-dur", default=0.3,   type=float, help="Duração mínima do silêncio em s (padrão: 0.3)")
    p.add_argument("--verbose", action="store_true", help="Modo verbose (debug)")
    return p


def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        log.error("FFmpeg não encontrado. Instale em: https://ffmpeg.org/download.html")
        sys.exit(1)


def main():
    parser = build_parser()
    args   = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    check_ffmpeg()

    cfg = SyncConfig(
        video_path  = args.video,
        script_path = args.script,
        output_path = args.output,
        tts_engine  = args.tts,
        language    = args.lang,
        bgm_path    = args.bgm,
        bgm_volume  = args.bgm_vol,
        preview_sec = args.preview,
        stretch     = not args.no_stretch,
        log_path    = args.log,
        silence_db  = args.silence_db,
        silence_dur = args.silence_dur,
    )

    engine = SyncEngine(cfg)
    t0 = time.time()
    try:
        out = engine.run()
        elapsed = time.time() - t0
        log.info("Concluído em %.1fs → %s", elapsed, out)
    except FileNotFoundError as e:
        log.error("Arquivo não encontrado: %s", e)
        sys.exit(2)
    except RuntimeError as e:
        log.error("Erro de processamento: %s", e)
        sys.exit(3)
    except KeyboardInterrupt:
        log.info("Interrompido pelo usuário.")
        sys.exit(0)
    finally:
        engine.cleanup()


if __name__ == "__main__":
    main()
