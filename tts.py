import sys
import asyncio
import edge_tts

async def main():
    texto = sys.argv[1]
    saida = sys.argv[2]
    communicate = edge_tts.Communicate(texto, "pt-BR-FranciscaNeural")
    await communicate.save(saida)

asyncio.run(main())
