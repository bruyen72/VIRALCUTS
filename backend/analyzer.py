"""LLM analysis to find viral moments in transcripts."""
import json, requests


SYSTEM_PROMPT = """Você é um especialista em criação de conteúdo viral para TikTok, Instagram Reels e YouTube Shorts.

Analise a transcrição e identifique os 5 melhores momentos para criar shorts virais.

REGRAS OBRIGATÓRIAS:
1. start e end são em SEGUNDOS (ex: start=120.5, end=165.0)
2. Duração de cada clip: MÍNIMO 20 segundos, MÁXIMO 58 segundos
3. start deve ser >= 0
4. end deve ser > start + 20

Retorne SOMENTE um array JSON válido, sem texto extra, sem markdown.
Formato exato:
[
  {"title":"Título Chamativo","hook":"Frase gancho de abertura...","start":0.0,"end":45.0,"score":95,"reason":"Motivo viral"},
  ...
]"""


def find_viral_moments(transcript: dict, api_key: str = '') -> list:
    """
    Use an LLM to identify the best moments for shorts.
    Falls back to segment-based heuristic if no key.
    """
    text     = transcript.get('text', '')
    segments = transcript.get('segments', [])

    if not text:
        return _heuristic_moments(segments)

    if api_key:
        try:
            result = _call_groq(text, segments, api_key)
            if result:
                return result
        except Exception as e:
            print(f'[Analyzer] LLM falhou: {e}, usando heurística')

    return _heuristic_moments(segments)


def _call_groq(text: str, segments: list, api_key: str) -> list:
    url     = 'https://api.groq.com/openai/v1/chat/completions'
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}

    # Get video total duration from last segment
    total_dur = segments[-1]['end'] if segments else 300

    user_content = (
        f'Duração total do vídeo: {total_dur:.1f} segundos\n\n'
        f'Transcrição com timestamps:\n'
    )
    for s in segments[:80]:
        user_content += f'[{s["start"]:.1f}s] {s["text"]}\n'

    payload = {
        'model':       'llama-3.3-70b-versatile',
        'messages':    [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user',   'content': user_content}
        ],
        'max_tokens':  1024,
        'temperature': 0.2,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    content = resp.json()['choices'][0]['message']['content'].strip()

    # Extract JSON array
    i_start = content.find('[')
    i_end   = content.rfind(']') + 1
    if i_start == -1 or i_end == 0:
        return []

    moments = json.loads(content[i_start:i_end])

    # Validate and fix timestamps
    validated = []
    for m in moments[:5]:
        s = float(m.get('start', 0))
        e = float(m.get('end', s + 45))
        dur = e - s

        # Fix: ensure duration between 15s and 60s
        if dur < 15:
            e = s + 45
        if dur > 60:
            e = s + 55
        # Ensure within video
        if e > total_dur:
            e = total_dur
        if s >= total_dur:
            continue

        m['start'] = round(s, 1)
        m['end']   = round(e, 1)
        validated.append(m)

    return validated


def _heuristic_moments(segments: list) -> list:
    """
    Simple heuristic when no LLM is available:
    Groups segments into ~30-45s clips and assigns mock scores.
    """
    if not segments:
        return _demo_moments()

    # Sort by position, group into clips of ~30-45s
    clips = []
    i = 0
    while i < len(segments) and len(clips) < 5:
        start_seg = segments[i]
        end_time  = start_seg['start'] + 45
        clip_segs = [s for s in segments if s['start'] >= start_seg['start'] and s['end'] <= end_time]
        if not clip_segs:
            i += 1
            continue
        text  = ' '.join(s['text'] for s in clip_segs)
        score = max(70, min(99, 70 + len(clip_segs) * 5))
        clips.append({
            'title':  _extract_title(text),
            'hook':   text[:80],
            'start':  start_seg['start'],
            'end':    clip_segs[-1]['end'],
            'score':  score - len(clips) * 3,
            'reason': 'Momento com alto engajamento identificado'
        })
        i += len(clip_segs)

    return clips if clips else _demo_moments()


def _extract_title(text: str) -> str:
    words = text.split()[:8]
    return ' '.join(words).rstrip('.,!?') or 'Momento Viral'


def _demo_moments() -> list:
    return [
        {'title': 'O Segredo do Sucesso',    'hook': 'Você nunca vai acreditar nisso...', 'start': 0,  'end': 45, 'score': 99, 'reason': 'Hook poderoso de abertura'},
        {'title': 'Como Ficar Rico em 2026', 'hook': 'A regra número 1 do dinheiro...',   'start': 45, 'end': 103,'score': 95, 'reason': 'Conteúdo financeiro tem alto compartilhamento'},
        {'title': 'Corte Engraçado Podcast', 'hook': 'E aí ele disse que...',              'start': 103,'end': 135,'score': 92, 'reason': 'Humor gera retenção'},
        {'title': 'Dica de Produtividade',   'hook': 'Pare de perder tempo com...',        'start': 135,'end': 175,'score': 88, 'reason': 'Dicas práticas têm alto watch-time'},
        {'title': 'História de Superação',   'hook': 'Quando eu estava no fundo...',       'start': 175,'end': 230,'score': 85, 'reason': 'Narrativa emocional aumenta retenção'},
    ]
