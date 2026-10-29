"""Estimated overall work, backed by real per-stage counters when available."""

import re

STAGES = (
    ("tools", 10), ("source", 2), ("extract", 10), ("sdk_download", 8),
    ("sdk_configure", 2), ("sdk_build", 38), ("codegen", 5),
    ("game_configure", 2), ("game_build", 20), ("assemble", 2), ("cleanup", 1),
)
NINJA_PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]")


class BuildProgress:
    def __init__(self):
        self.key = None
        self.value = 0.0
        self.known = False

    def begin(self, key):
        if key not in dict(STAGES):
            raise ValueError(f"Unknown build stage: {key}")
        self.key = key
        self.known = False
        self.update(None, None)

    def update(self, done, total):
        if self.key is None:
            return
        floor = 0
        for key, weight in STAGES:
            if key == self.key:
                self.known = total is not None and total > 0 and done is not None and done >= 0
                fraction = min(done / total, 1) if self.known else 0
                self.value = min(99.0, max(self.value, floor + fraction * weight))
                return
            floor += weight

    def finish(self):
        self.value = 100.0
        self.known = True

    def label(self):
        if self.value == 100:
            return "100% complete"
        suffix = "" if self.known else " · current step has no fixed total"
        return f"~{self.value:.0f}% overall (estimated work){suffix}"
