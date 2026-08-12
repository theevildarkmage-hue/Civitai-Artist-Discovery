from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import AdaptivePacer


pacer = AdaptivePacer()
assert pacer.interval == 1.0

# A short clean run should remain stable; sustained success recovers gradually.
for _ in range(9):
    pacer.success(0.5)
assert pacer.interval == 1.0
pacer.success(0.5)
assert abs(pacer.interval - 0.9) < 0.0001

# A slow serialized response naturally spaces calls; it pauses acceleration but
# does not add a second artificial delay without an actual service error.
pacer.success(3.2)
assert abs(pacer.interval - 0.9) < 0.0001
assert pacer.clean_streak == 0

# A rate limit causes an immediate, substantial slowdown.
pacer.failure("rate_limited")
assert pacer.interval == 5.0

# Recovery is intentionally gradual and cannot cross the configured floor.
for _ in range(500):
    pacer.success(0.4)
assert pacer.interval == pacer.minimum == 0.75

# Repeated trouble is bounded so the foreground app remains cancellable.
for _ in range(10):
    pacer.failure("rate_limited")
assert pacer.interval == pacer.maximum == 8.0

print({"adaptivePacing": "ok", "minimum": pacer.minimum, "maximum": pacer.maximum})
