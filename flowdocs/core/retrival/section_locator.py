# core/retrieval/section_locator.py

SECTION_KEYWORDS = [
    "व्याख्या",
    "याचा अर्थ",
    "परिभाषा",
    "१५४",
]


def locate_legal_definition(documents: list[str]) -> str | None:
    """
    Housing-specific: find legal definition.
    Returns FIRST matching section.
    """
    print("📜 section_locator: scanning for legal section")

    for doc in documents:
        lines = doc.split("\n")
        for line in lines:
            for key in SECTION_KEYWORDS:
                if key in line:
                    print("✅ Legal section located")
                    return line.strip()

    print("❌ No legal section found")
    return None