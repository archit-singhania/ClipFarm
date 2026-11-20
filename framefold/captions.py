"""Portable burned captions for FFmpeg builds without the libass filter."""
import os
import textwrap


def overlay_inputs(cues, directory, width, height, duration):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ValueError("Install Pillow or use an FFmpeg build with the subtitles filter, then retry.") from exc
    font_path = os.environ.get("FRAMEFOLD_CAPTION_FONT")
    font_size = round(min(width, height) * .047)
    try:
        font = ImageFont.truetype(font_path or "DejaVuSans.ttf", font_size)
    except OSError:
        font = ImageFont.load_default(size=font_size)
    strip = font_size * 5
    Image.new("RGBA", (width, strip)).save(directory / "blank.png")
    playlist = ["ffconcat version 1.0"]
    position = 0

    def show(name, millis):
        if millis > 0:
            playlist.extend((f"file '{name}'", f"duration {millis/1000:.3f}"))

    for index, (start, end, text) in enumerate(cues):
        if end <= position:
            continue
        start = max(start, position)
        show("blank.png", start-position)
        name = f"cue-{index}.png"
        image = Image.new("RGBA", (width, strip))
        draw = ImageDraw.Draw(image)
        lines = textwrap.fill(text, width=max(20, int(width / font_size * 1.55)), max_lines=3,
                              placeholder="…")
        draw.multiline_text((width/2, strip-font_size*.3), lines, font=font, anchor="md",
                            align="center", fill="white", stroke_width=max(2, font_size//12),
                            stroke_fill="#111016", spacing=round(font_size*.12))
        image.save(directory / name)
        show(name, end-start)
        position = end
    show("blank.png", round(duration*1000)-position)
    playlist.append("file 'blank.png'")
    path = directory / "cues.ffconcat"
    path.write_text("\n".join(playlist) + "\n")
    return ["-protocol_whitelist", "file", "-f", "concat", "-safe", "1", "-i", str(path)]
