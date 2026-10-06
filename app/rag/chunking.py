"""Chunking simple por palabras con overlap.

Suficiente para el tamano y forma actual de la KB (articulos cortos,
~150-350 palabras, texto plano/markdown sin tablas ni bloques de
codigo grandes). Si la KB crece mucho o el contenido se vuelve mas
heterogeneo, este es el primer candidato a reemplazar por un chunker
consciente de estructura markdown (por encabezados) -- no antes,
porque agregar esa complejidad ahora no tiene evidencia detras
todavia.
"""

from __future__ import annotations

_CHUNK_SIZE_WORDS = 150
_CHUNK_OVERLAP_WORDS = 30


def chunk_text(
    text: str,
    chunk_size: int = _CHUNK_SIZE_WORDS,
    overlap: int = _CHUNK_OVERLAP_WORDS,
) -> list[str]:
    """Parte el texto en ventanas de `chunk_size` palabras con
    `overlap` palabras compartidas entre ventanas consecutivas -- el
    overlap evita que una idea que cae justo en el borde de un chunk
    quede partida sin contexto en ninguno de los dos lados."""
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = end - overlap
    return chunks
