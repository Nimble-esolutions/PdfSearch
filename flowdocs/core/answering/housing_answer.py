# core/answering/housing_answer.py

from core.retrival.faiss_manager import retrieve_documents
from core.prompting.strict_legal import HOUSING_LEGAL_PROMPT
from core.utils.language import normalize_marathi
from openai import OpenAI

client = OpenAI()


def generate_housing_answer(user_query: str) -> str:
    """
    Housing answers MUST:
    - Use only ACT / RULE / BYLAW content
    - Refer strictly to retrieved legal text
    - Never hallucinate
    """

    print("\n🏛️ housing_answer.py | generate_housing_answer")

    user_query = normalize_marathi(user_query)

    # 🔑 HARD RULE: housing only
    allowed_folders = ["housing"]

    # 🔍 Retrieve legal chunks
    chunks = retrieve_documents(
        query=user_query,
        allowed_folders=allowed_folders,
        limit=100
    )

    if not chunks:
        print("⚠️ No legal chunks found")
        return (
            "सदर प्रश्नासाठी अधिनियम, नियम किंवा उपविधीतील "
            "संबंधित मजकूर उपलब्ध नाही."
        )

    # 📚 Build legal context
    legal_context = "\n\n".join(chunks)

    prompt = HOUSING_LEGAL_PROMPT.format(
        context=legal_context,
        question=user_query
    )

    print("🧠 Sending prompt to LLM")
    print(prompt)
    
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a legal assistant for Maharashtra Housing laws."},
            {"role": "user", "content": prompt},
        ],
        temperature=0
    )

    answer = response.choices[0].message.content.strip()

    print("✅ Housing answer generated")
    return answer