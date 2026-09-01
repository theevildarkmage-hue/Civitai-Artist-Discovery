from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import AdaptivePacer


pacer = AdaptivePacer()
assert pacer.interval == 1.0

# A short clean run should remain stable; sustained success recovers gradually.
for _ in range(AdaptivePacer.RECOVERY_STREAK - 1):
    pacer.success(0.5)
assert pacer.interval == 1.0
pacer.success(0.5)
assert abs(pacer.interval - 1.0 * AdaptivePacer.RECOVERY_FACTOR) < 0.0001

# A slow serialized response naturally spaces calls; it pauses acceleration but
# does not add a second artificial delay without an actual service error.
pacer.success(3.2)
assert abs(pacer.interval - 1.0 * AdaptivePacer.RECOVERY_FACTOR) < 0.0001
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

# Recovery has to finish inside a normal collection. Subtracting a fixed step took about
# 725 clean requests to come down from the ceiling -- longer than most runs -- so any
# hiccup left the app slow for the rest of it and often the next one too.
recovering = AdaptivePacer()
for _ in range(10):
    recovering.failure("rate_limited")
assert recovering.interval == recovering.maximum
requests = 0
while recovering.interval > recovering.minimum and requests < 5000:
    recovering.success(0.4)
    requests += 1
assert recovering.interval == recovering.minimum, recovering.interval
assert requests <= 120, f"recovery took {requests} clean requests"

# Climbing still outpaces recovery: a service that just failed is approached carefully.
climb = AdaptivePacer()
climb.failure("service_retry")
steps_up = 1
while climb.interval < climb.maximum:
    climb.failure("service_retry")
    steps_up += 1
down = AdaptivePacer()
for _ in range(10):
    down.failure("rate_limited")
steps_down = 0
while down.interval > down.minimum:
    for _ in range(AdaptivePacer.RECOVERY_STREAK):
        down.success(0.4)
    steps_down += 1
assert steps_down > steps_up, (steps_down, steps_up)

print({"adaptivePacing": "ok", "minimum": pacer.minimum, "maximum": pacer.maximum})
