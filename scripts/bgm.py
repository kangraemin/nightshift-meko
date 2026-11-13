"""Quiet ambient bed for the demo video, generated here (no samples, no loop).

    python scripts/bgm.py <seconds> <out.wav>
Slow pad chords picked by a seeded random walk, so nothing repeats on a fixed cycle.
"""
import sys
import wave

import numpy as np

SR = 44100
CHORDS = [[57, 60, 64, 69], [53, 57, 60, 65], [55, 59, 62, 67], [52, 55, 59, 64], [50, 53, 57, 62], [48, 52, 55, 60]]


def hz(m):
    return 440 * 2 ** ((m - 69) / 12)


def main():
    secs, out = float(sys.argv[1]), sys.argv[2]
    rng = np.random.default_rng(7)
    n = int(secs * SR)
    buf = np.zeros(n)
    t0, ci = 0.0, 0
    while t0 < secs:
        d = rng.uniform(6.5, 11.0)
        ci = (ci + rng.choice([1, 2, 3, 5])) % len(CHORDS)
        a, b = int(t0 * SR), min(n, int((t0 + d + 3.0) * SR))
        t = np.arange(b - a) / SR
        env = np.minimum(1, t / 2.5) * np.minimum(1, (b - a) / SR - t) ** 1.0
        env = np.clip(env, 0, 1)
        for m in CHORDS[ci]:
            f = hz(m - 12) * (1 + rng.uniform(-0.002, 0.002))
            ph = rng.uniform(0, 2 * np.pi)
            buf[a:b] += env * (np.sin(2 * np.pi * f * t + ph) + 0.25 * np.sin(4 * np.pi * f * t + ph)) / 4
        t0 += d
    # soft low-pass by moving average, then normalize
    k = 32
    buf = np.convolve(buf, np.ones(k) / k, mode="same")
    fade = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (2 * SR))
    buf = buf * fade
    buf = buf / (np.abs(buf).max() + 1e-9) * 0.5
    pcm = (buf * 32767).astype(np.int16)
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    main()
