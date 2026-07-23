"""Pre-merge migration guard — catch numbering collisions and gaps.

Run from the repo root:
    python scripts/ci/validate_migrations.py [--base dev] [--check-tests]

Rules enforced:
    1. Migration numbers in the current branch are sequential (no gaps).
    2. Migration numbers do not collide with those already on the base branch.
    3. (optional) No two test classes share the same name.
"""

import argparse
import os
import re
import sys
from pathlib import Path

MIGRATION_RE = re.compile(r"^(\d{4})_.+\.py$")
TEST_CLASS_RE = re.compile(r"^class\s+(\w+)Tests?\s*[\(:]")


def migration_number(path: Path) -> int | None:
    m = MIGRATION_RE.match(path.name)
    return int(m.group(1)) if m else None


def collect_migrations(migration_dir: Path) -> list[int]:
    numbers = []
    for p in migration_dir.glob("*.py"):
        if p.name == "__init__.py":
            continue
        n = migration_number(p)
        if n is not None:
            numbers.append(n)
    return sorted(numbers)


def find_test_classes(test_dir: Path) -> dict[str, list[Path]]:
    """Return {class_name: [file_paths]} for duplicate detection."""
    classes: dict[str, list[Path]] = {}
    for py_file in sorted(test_dir.rglob("test*.py")):
        for line in py_file.read_text().splitlines():
            m = TEST_CLASS_RE.match(line)
            if m:
                name = m.group(1)
                classes.setdefault(name, []).append(py_file)
    return classes


def check_migration_sequence(numbers: list[int], label: str) -> list[str]:
    errors = []
    if not numbers:
        return errors
    expected = numbers[0]
    for n in numbers:
        if n != expected:
            errors.append(
                f"{label}: gap or out-of-order migration: expected {expected:04d}, found {n:04d}"
            )
        expected = n + 1
    return errors


def check_no_collision(branch_numbers: list[int], base_numbers: list[int]) -> list[str]:
    errors = []
    base_set = set(base_numbers)
    for n in branch_numbers:
        if n in base_set:
            errors.append(
                f"Migration collision: {n:04d} exists in both the branch and the base branch. "
                f"Rebase onto the base branch and renumber."
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-merge migration guard")
    parser.add_argument("--base", default="origin/dev", help="Base branch to compare against")
    parser.add_argument("--check-tests", action="store_true", help="Also check for duplicate test class names")
    parser.add_argument("--migration-dir", default="flowdocs/core/migrations", help="Path to Django migrations")
    parser.add_argument("--test-dir", default="flowdocs/core", help="Path to test directory")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    migration_dir = repo_root / args.migration_dir
    test_dir = repo_root / args.test_dir
    errors: list[str] = []

    if not migration_dir.is_dir():
        print(f"Migrations directory not found: {migration_dir}")
        return 0

    branch_numbers = collect_migrations(migration_dir)
    errors.extend(check_migration_sequence(branch_numbers, "branch"))

    if args.base:
        import subprocess
        try:
            result = subprocess.run(
                ["git", "show", f"{args.base}:{args.migration_dir}"],
                capture_output=True, text=True, cwd=repo_root,
            )
            if result.returncode == 0:
                base_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
                base_numbers = []
                for fname in base_files:
                    fname = fname.strip()
                    m = MIGRATION_RE.match(fname)
                    if m:
                        base_numbers.append(int(m.group(1)))
                base_numbers.sort()
                errors.extend(check_no_collision(branch_numbers, base_numbers))
        except Exception as exc:
            print(f"Warning: could not read base branch migrations: {exc}")

    if args.check_tests:
        classes = find_test_classes(test_dir)
        for cls_name, paths in classes.items():
            if len(paths) > 1:
                errors.append(
                    f"Duplicate test class '{cls_name}' in: "
                    + ", ".join(str(p.relative_to(repo_root)) for p in paths)
                )

    if errors:
        print(f"\n{len(errors)} pre-merge validation error(s):\n")
        for e in errors:
            print(f"  - {e}")
        print(f"\nFix these before merging.\n")
        return 1

    print("Pre-merge migration guard: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
