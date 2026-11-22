"""Make a portable local review page for one source's candidate JSONL."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
from urllib.parse import quote


def build(candidates, video, output):
    rows = [json.loads(line) for line in candidates.read_text().splitlines() if line.strip()]
    if not rows or len({row["source"] for row in rows}) != 1:
        raise ValueError("Choose a candidate file containing exactly one original video.")
    if not video.is_file():
        raise ValueError("The original video is missing.")
    # Hide model scores and review one candidate at a time, in a reproducible order.
    rows.sort(key=lambda row: hashlib.sha256(
        f"{row['source']}/{row['start_ms']}/{row['end_ms']}".encode()
    ).digest())
    url = quote(os.path.relpath(video.resolve(), output.resolve().parent))
    template = Path(__file__).with_name("review.html").read_text()
    encoded = json.dumps(rows).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output.write_text(template.replace("{{VIDEO}}", html.escape(url, quote=True)).replace("{{ROWS}}", encoded))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.candidates, args.video, args.output)
