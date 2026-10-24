"""Потоковое чтение большого файла по HTTP Range-запросам с повторами (в stdout).

Используется для вырезания региона из планетарного дампа OSM без сохранения 95 ГБ на диск:
  python scripts/stream_s3.py https://osm-pds.s3.amazonaws.com/2026/planet-261019.osm.pbf \
    | osmium extract -b 39.0,43.35,40.45,44.15 --strategy simple -F pbf - -o sochi.osm.pbf
"""

import sys
import time
import urllib.request

CHUNK = 512 * 1024 * 1024


def total_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def main() -> None:
    url = sys.argv[1]
    size = total_size(url)
    out = sys.stdout.buffer
    pos = 0
    while pos < size:
        end = min(size, pos + CHUNK) - 1
        for attempt in range(8):
            try:
                req = urllib.request.Request(url, headers={"Range": f"bytes={pos}-{end}"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    while True:
                        buf = r.read(4 * 1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
                        pos += len(buf)
                if pos > end:
                    break
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"retry at {pos}: {exc}\n")
                time.sleep(2**attempt)
        else:
            raise SystemExit(f"failed at {pos}")
        sys.stderr.write(f"{pos / size * 100:.1f}%\n")
    out.flush()


if __name__ == "__main__":
    main()
