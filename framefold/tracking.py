"""Motion-guided crop paths for a single shot. Coordinates use display orientation."""
import math
import statistics

GRID_W, GRID_H = 48, 32
SAMPLE_FPS = 6


def motion_center(previous, current):
    """Locate compact moving foreground. Reject cuts and diffuse camera motion."""
    differences = [abs(a - b) for a, b in zip(previous, current)]
    threshold = max(14, statistics.median(differences) * 2.5)
    active = [i for i, delta in enumerate(differences) if delta > threshold]
    if not 3 <= len(active) <= GRID_W * GRID_H * .28:
        return None

    # Suppress isolated noise before choosing the strongest coherent neighborhood.
    weights = {}
    for i in active:
        x, y = i % GRID_W, i // GRID_W
        cell = (x // 6, y // 4)
        weights[cell] = weights.get(cell, 0) + differences[i]

    def neighborhood(cell):
        x, y = cell
        return sum(weights.get((x + dx, y + dy), 0) for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    peak = max(weights, key=neighborhood)
    concentrated = [i for i in active if abs(i % GRID_W // 6 - peak[0]) <= 1
                    and abs(i // GRID_W // 4 - peak[1]) <= 1]
    mass = sum(differences[i] for i in concentrated)
    if mass < .65 * sum(weights.values()):
        return None
    x = sum((i % GRID_W + .5) * differences[i] for i in concentrated) / (mass * GRID_W)
    y = sum((i // GRID_W + .5) * differences[i] for i in concentrated) / (mass * GRID_H)
    return x, y


def crop_path(frames):
    size = GRID_W * GRID_H
    centers = [None]
    for offset in range(size, len(frames), size):
        centers.append(motion_center(frames[offset-size:offset], frames[offset:offset+size]))
    valid = [i for i, center in enumerate(centers) if center is not None]
    if len(valid) < max(3, math.ceil(.30 * len(centers))):
        return []
    if any(right-left > SAMPLE_FPS*2 for left, right in zip(valid, valid[1:])):
        return []
    if valid[0] > SAMPLE_FPS*2 or len(centers)-1-valid[-1] > SAMPLE_FPS*2:
        return []

    path = []
    cursor = 0
    for index in range(len(centers)):
        while cursor+1 < len(valid) and valid[cursor+1] <= index:
            cursor += 1
        left = valid[cursor]
        right = valid[min(cursor+1, len(valid)-1)]
        blend = min(1, max(0, (index-left) / max(1, right-left)))
        path.append(tuple(a+(b-a)*blend for a, b in zip(centers[left], centers[right])))
    # A short symmetric window dampens jitter without introducing camera lag.
    return [tuple(statistics.mean(point[axis] for point in path[max(0, i-2):i+3])
                  for axis in (0, 1)) for i in range(len(path))]


def tracking_filter(frames, source_width, source_height, width, height, temporary):
    path = crop_path(frames)
    if not path:
        return None
    crop_width = max(2, 2 * int(min(source_width, source_height * width / height) / 2))
    crop_height = max(2, 2 * int(min(source_height, source_width * height / width) / 2))

    def origin(point, extent, window):
        return 2 * round(max(0, min(extent-window, point*extent-window/2)) / 2)

    commands = []
    for i, center in enumerate(path):
        following = path[min(i+1, len(path)-1)]
        for frame in range(30 // SAMPLE_FPS):
            fraction = frame / (30 // SAMPLE_FPS)
            x, y = (a+(b-a)*fraction for a, b in zip(center, following))
            x = origin(x, source_width, crop_width)
            y = origin(y, source_height, crop_height)
            if not commands:
                initial = (x, y)
            at = i/SAMPLE_FPS + frame/30
            commands.append(f"{at:.6f} crop@subject x {x}, crop@subject y {y};")
    script = temporary / "camera.txt"
    script.write_text("\n".join(commands))
    return (f"fps=30,sendcmd=f={temporary.name}/camera.txt,"
            f"crop@subject={crop_width}:{crop_height}:{initial[0]}:{initial[1]},scale={width}:{height}")
