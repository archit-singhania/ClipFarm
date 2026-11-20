"""Build a static example from a verified ZIP exported through Framefold."""
import argparse
import html
import json
from pathlib import Path
import re
import shutil
import zipfile

WEB = Path(__file__).resolve().parents[1] / "web"


def build(archive, output, name):
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Choose an empty output directory.")
    cards = []
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        for clip in manifest["clips"]:
            bits = clip["id"]
            if not re.fullmatch(r"[a-f0-9]{32}", bits) or not clip["artifact"]["verified"]:
                raise ValueError("The archive contains an unverified clip.")
            filename = f"pick-{int(clip['rank']):02}-{bits[:8]}"
            with bundle.open(filename + ".mp4") as source, (output / (filename + ".mp4")).open("xb") as target:
                shutil.copyfileobj(source, target)
            title = f"Pick {int(clip['rank']):02}" + (" · Copy" if clip.get("parent") else "")
            captions = []
            for extension in ("srt", "vtt"):
                entry = filename + "." + extension
                if entry in bundle.namelist():
                    (output / entry).write_bytes(bundle.read(entry))
                    captions.append(f'<a class="button" href="{entry}" download>↓ {extension.upper()}</a>')
            cards.append(f"""
            <article class="card">
              <div class="card-art"><video controls playsinline preload="metadata"
                src="{filename}.mp4" aria-label="{title}"></video></div>
              <div class="card-body"><h3>{title}</h3>
              <p class="meta">{html.escape(clip['range_label'])} · {int(clip['artifact']['width'])} × {int(clip['artifact']['height'])}</p>
              <p>{html.escape(clip['reason'])}</p>
              <div class="actions"><a class="button" href="{filename}.mp4" download>↓ Download</a>
              {''.join(captions)}</div></div>
            </article>""")
    if not cards:
        raise ValueError("The archive contains no verified clips.")
    index = (WEB / "index.html").read_text()
    header = index[index.index('  <header'):index.index('  <main')]
    header = re.sub(r'<nav.*?</nav>', '', header)
    header = header.replace('href="#/"', 'href="./"').replace('Checking worker', 'Example run')
    page = f"""<!doctype html>
<html lang="en" data-theme="charcoal">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Framefold · Example run</title>
  <script src="theme.js"></script>
  <link rel="stylesheet" href="style.css">
</head>
<body>
{header}
<main class="shell">
  <div class="heading-row"><div><h1>Example <em>run.</em></h1>
  <p class="lede">{html.escape(name)} · {len(cards)} ready-to-post clips</p></div>
  <a class="button primary" href="framefold-clips.zip" download>↓ Download all</a></div>
  <p class="muted">This demo plays pre-rendered files. Use the local app to generate clips from your own sources.</p>
  <section class="section"><h2>Ranked picks</h2><div class="grid">{''.join(cards)}</div></section>
</main>
</body>
</html>
"""
    (output / "index.html").write_text(page)
    for asset in ("theme.js", "style.css"):
        shutil.copy2(WEB / asset, output / asset)
    shutil.copy2(archive, output / "framefold-clips.zip")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--name", required=True, help="Display name for a release-cleared source")
    options = parser.parse_args()
    build(options.archive, options.output, options.name)
