# core/answering/generic_answer.py

from core.prompting.general import GENERAL_PROMPT
from openai import OpenAI

client = OpenAI()

def generate_generic_answer(context: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": GENERAL_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0.3,
    )

    return response.choices[0].message.content.strip()
