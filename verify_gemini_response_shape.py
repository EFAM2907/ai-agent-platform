"""
One-off script: run locally with your real Gemini API key to confirm
the exact shape of a real response -- usage_metadata field names and
the finish_reason value. Delete after using it.
"""

import asyncio

from app.core.config import settings
from google import genai


async def main() -> None:
    client = genai.Client(api_key=settings.gemini_api_key)

    response = await client.aio.models.generate_content(
        model="gemini-3.6-flash",
        contents=[{"role": "user", "parts": [{"text": "di 'hola' y nada más"}]}],
    )

    print("--- response.text ---")
    print(response.text)
    print("--- response.usage_metadata ---")
    print(response.usage_metadata)
    print("--- response.candidates[0].finish_reason ---")
    print(response.candidates[0].finish_reason)
    print("--- type(finish_reason) ---")
    print(type(response.candidates[0].finish_reason))


if __name__ == "__main__":
    asyncio.run(main())