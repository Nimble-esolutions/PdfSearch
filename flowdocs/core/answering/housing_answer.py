# core/answering/housing_answer.py

from core.retrival.faiss_manager import retrieve_documents
from core.prompting.strict_legal import STRICT_LEGAL_PROMPT
from core.utils.language import normalize_marathi
from openai import OpenAI
from core.models import PDFFile,Folder


client = OpenAI()
def generate_housing_answer(user_query: str):
    """
    Housing answers MUST:
    - Use only ACT / RULE / BYLAW content
    - Refer strictly to retrieved legal text
    - Never hallucinate
    """
    print("\n🏛️ housing_answer.py | generate_housing_answer")

    # Normalize Marathi input
    user_query = normalize_marathi(user_query)
    print("❓ Question:", user_query)

    # 🔑 HARD RULE: housing only (folder-name based)
    allowed_folders = ["Housing"]

    # 🔍 Retrieve legal chunks
    chunks = retrieve_documents(
        query=user_query,
        allowed_folders=allowed_folders,
        limit=100
    )

    print("📦 Retrieved chunks count:", len(chunks))

    if not chunks:
        print("⚠️ No legal chunks found")
        return {
            "answer": (
                "सदर प्रश्नासाठी अधिनियम, नियम किंवा उपविधीतील "
                "संबंधित मजकूर उपलब्ध नाही."
            ),
            "references": []
        }

    # 📚 Build legal context (TEXT ONLY sent to LLM)
    legal_context = "\n\n".join(c["text"] for c in chunks)

    prompt = STRICT_LEGAL_PROMPT.format(
        context=legal_context,
        question=user_query
    )

    print("🧠 Sending prompt to LLM")

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a legal assistant for Maharashtra Housing laws."},
            {"role": "user", "content": prompt},
        ],
        temperature=0
    )

    answer = response.choices[0].message.content.strip()
    print("Answer generated:", answer)

    # 📎 Collect unique reference PDFs
    used_pdfs = {}

    for c in chunks:
        pdf_id = c.get("pdf_id")
        if not pdf_id or pdf_id in used_pdfs:
            continue

        try:
            pdf_obj = PDFFile.objects.get(id=pdf_id)

            if pdf_obj.file:
                # Use folder name if available, otherwise "Root"
                folder_path = pdf_obj.folder.name if pdf_obj.folder else "Root"

                used_pdfs[pdf_id] = {
                    "title": pdf_obj.title,
                    "url": pdf_obj.file.url,
                    "folder": folder_path,
                    "uploaded_at": pdf_obj.uploaded_at.strftime("%d-%m-%Y"),
                }

        except PDFFile.DoesNotExist:
            print(f"⚠️ PDFFile with id {pdf_id} does not exist")
            continue

    references = list(used_pdfs.values())

    print("✅ Housing answer generated")
    print("📚 Reference PDFs:", references)

    return {
        "answer": answer,
        "references": references
    }
