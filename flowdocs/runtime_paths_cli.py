"""Print one verified active-runtime path for shell startup scripts."""

from __future__ import annotations

import argparse

from vaultops.runtime_control import RuntimeControlError, resolve_runtime_from_env


FIELDS = {
    "database": "database_path",
    "media": "media_root",
    "pdf_cache": "pdf_cache_dir",
    "faiss": "faiss_index_dir",
    "chroma": "chroma_dir",
    "generation": "generation_id",
}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("field", choices=sorted(FIELDS))
    options = parser.parse_args(argv)
    try:
        runtime = resolve_runtime_from_env()
    except RuntimeControlError as exc:
        parser.error(exc.reason_code)
    if runtime is None:
        parser.error("staging_activation_disabled")
    print(getattr(runtime, FIELDS[options.field]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
