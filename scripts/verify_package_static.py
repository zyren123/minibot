from __future__ import annotations

import pathlib
import re
import sys
import tarfile
import zipfile


def _load_archive_members(archive_path: pathlib.Path) -> tuple[list[str], str]:
    suffixes = archive_path.suffixes
    if suffixes[-1:] == [".whl"] or suffixes[-2:] == [".tar", ".gz"]:
        pass
    else:
        raise SystemExit(f"unsupported archive format: {archive_path}")

    if archive_path.suffix == ".whl":
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            index_name = "minibot/server/static/index.html"
            try:
                index_html = archive.read(index_name).decode("utf-8")
            except KeyError as exc:
                raise SystemExit(f"missing {index_name} in {archive_path}") from exc
        return names, index_html

    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()
        index_name = next((name for name in names if name.endswith("/src/minibot/server/static/index.html")), None)
        if index_name is None:
            raise SystemExit(f"missing src/minibot/server/static/index.html in {archive_path}")
        index_member = archive.extractfile(index_name)
        if index_member is None:
            raise SystemExit(f"unable to read {index_name} in {archive_path}")
        index_html = index_member.read().decode("utf-8")
    return names, index_html


def _normalize_asset_names(archive_path: pathlib.Path, names: list[str]) -> list[str]:
    if archive_path.suffix == ".whl":
        prefix = "minibot/server/static/assets/"
    else:
        prefix = "/src/minibot/server/static/assets/"

    assets = []
    for name in names:
        if prefix not in name:
            continue
        asset_name = name.split(prefix, 1)[1]
        if asset_name and not asset_name.endswith("/"):
            assets.append(asset_name)
    return sorted(assets)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("usage: python scripts/verify_package_static.py <archive> [<archive> ...]")

    for raw_path in argv[1:]:
        archive_path = pathlib.Path(raw_path)
        names, index_html = _load_archive_members(archive_path)
        refs = sorted(set(re.findall(r'/assets/([^" ]+)', index_html)))
        if not refs:
            raise SystemExit(f"no /assets/ references found in {archive_path}")

        assets = _normalize_asset_names(archive_path, names)
        if refs != assets:
            raise SystemExit(
                f"asset mismatch in {archive_path}: index references {refs}, archive contains {assets}"
            )

        print(f"{archive_path}: ok ({len(refs)} assets)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
