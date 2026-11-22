"""Public configuration names, with compatibility for existing installations."""
import os


def setting(name, default=None):
    return os.environ.get(f"CLIPFARM_{name}", os.environ.get(f"FRAMEFOLD_{name}", default))
