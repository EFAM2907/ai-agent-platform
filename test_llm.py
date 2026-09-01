import asyncio

from app.llm.client import LLMClient


async def main() -> None:
    llm = LLMClient()

    response = await llm.generate(
        prompt="Explica qué es PostgreSQL en una frase.",
        model="gpt-5-mini",
    )

    print(response)


if __name__ == "__main__":
    asyncio.run(main())