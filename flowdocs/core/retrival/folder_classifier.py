# core/retrieval/folder_classifier.py

HOUSING_KEYWORDS = [
    "गृहनिर्माण",
    "भाडेकरू",
    "सह-भागीदारी",
    "पुनर्विकास",
    "सदनिका",
    "हाउसिंग",
]


def is_housing_query(query: str) -> bool:
    return any(word in query for word in HOUSING_KEYWORDS)


def classify_folders(query: str) -> list[str]:
    """
    Primary domain decision.
    This is the FIRST gate.
    """
    if is_housing_query(query):
        print("🔒 folder_classifier: Housing domain locked")
        return [
            "Housing Act",
            "Housing Rules",
            "Housing Bylaws",
        ]

    print("📁 folder_classifier: Generic domain")
    return [
        "Housing Act",
        "Audit",
        "AgriCredit",
    ]