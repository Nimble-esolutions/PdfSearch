from datetime import date


def legal_last_updated(request):
    return {
        "legal_last_updated": date(2026, 7, 24).strftime("%d %B %Y"),
    }
