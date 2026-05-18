import asyncio, sys, edge_tts

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

async def gerar(texto, output):
    communicate = edge_tts.Communicate(texto, voice="pt-BR-FranciscaNeural")
    await communicate.save(output)

if __name__ == "__main__":
    texto = sys.argv[1]
    output = sys.argv[2]
    asyncio.run(gerar(texto, output))
