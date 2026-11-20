"""Minimal MIT routines used by the export path. See third_party/."""

# OpenShorts, ffmpeg_utils.py.
METADATA_SCRUB = ["-map_metadata", "-1", "-map_chapters", "-1",
                  "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1"]


# opensource-clipping, clipping/studio/ffmpeg_utils.py.
def format_seconds(seconds):
    """Format seconds as HH:MM:SS."""
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"
