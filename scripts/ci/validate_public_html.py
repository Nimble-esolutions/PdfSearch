#!/usr/bin/env python
"""Validate that the public search page has required SEO elements.

Usage:
    curl -fsS http://localhost:8000/ | python scripts/ci/validate_public_html.py
    python scripts/ci/validate_public_html.py < file.html
"""

import sys
import json
import re


def validate(html: str) -> list[str]:
    issues = []

    if "<title>" not in html or "</title>" not in html:
        issues.append("missing-title")
    elif "Sahakar AI" not in html.split("<title>")[1].split("</title>")[0]:
        issues.append("title-missing-brand")

    if 'name="description"' not in html:
        issues.append("missing-description")

    if 'rel="canonical"' not in html:
        issues.append("missing-canonical")

    if 'property="og:title"' not in html:
        issues.append("missing-og-title")

    if 'property="og:description"' not in html:
        issues.append("missing-og-description")

    if 'name="twitter:card"' not in html:
        issues.append("missing-twitter-card")

    json_ld_blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    if not json_ld_blocks:
        issues.append("missing-json-ld")
    else:
        for block in json_ld_blocks:
            try:
                data = json.loads(block.strip())
                if "@type" not in data:
                    issues.append("json-ld-missing-type")
            except json.JSONDecodeError:
                issues.append("json-ld-invalid-json")

    if "innerHTML" in html:
        issues.append("uses-innerHTML")

    if "|safe" in html:
        issues.append("uses-template-safe-filter")

    if 'rel="noopener noreferrer"' not in html:
        issues.append("missing-noopener-on-external-links")

    if "Answers are informational" not in html:
        issues.append("missing-disclaimer")

    return issues


def main():
    html = sys.stdin.read()
    issues = validate(html)
    if issues:
        print(f"FAIL: {', '.join(issues)}", file=sys.stderr)
        sys.exit(1)
    print("OK: all SEO checks passed")


if __name__ == "__main__":
    main()
