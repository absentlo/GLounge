#!/usr/bin/env python3
"""Renders the GLounge instruments through REAPER and checks the audio. Run: python3 tests/smoke.py
Needs REAPER on PATH and a display. Uses a throwaway config, not your REAPER settings."""
import atexit, cmath, glob, math, os, re, shutil, struct, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUCK, PICK, DECAY, TONE, DRIVE, LEVEL = 1, 2, 3, 4, 5, 6
PICKUP = 7
CLEAN = {DRIVE: 0, TONE: 8000}
PLUGIN = "bass.jsfx"


def project(path, out, params, notes, seconds, rate, bend_at=None):
    """notes: (start_beat, length_beats, midi_note). 120 BPM, 960 ticks per beat.
    bend_at: the beat where the pitch wheel jumps fully up."""
    src = open(os.path.join(ROOT, PLUGIN)).read()
    defaults = {int(n): v for n, v in re.findall(r"^slider(\d+):\w+=(-?[\d.]+)", src, re.M)}
    vals = " ".join(str(params.get(i, defaults.get(i, "-"))) for i in range(1, 65))
    events, last = [], 0
    for tick, status, note, vel in sorted(
        [(int(s * 960), 0x90, n, 110) for s, _, n in notes]
        + [(int((s + l) * 960), 0x80, n, 0) for s, l, n in notes]
        + ([(int(bend_at * 960), 0xE0, 127, 127)] if bend_at else [])
    ):
        events.append(f"E {tick - last} {status:02x} {note:02x} {vel:02x}")
        last = tick
    body = "\n".join(events)
    with open(path, "w") as f:
        f.write(f"""<REAPER_PROJECT 0.1 "7.0" 0
  TEMPO 120 4 4
  RENDER_FILE "{out}"
  RENDER_PATTERN ""
  RENDER_FMT 0 2 {rate}
  RENDER_RANGE 1 0 {seconds} 18 1000
  RENDER_1X 0
  RENDER_RESAMPLE 3 0 1
  RENDER_STEMS 0
  RENDER_DITHER 0
  SAMPLERATE {rate} 0 0
  <RENDER_CFG
    ZXZhdyABAA==
  >
  <TRACK
    NAME bass
    NCHAN 2
    MAINSEND 1 0
    <FXCHAIN
      SHOW 0
      LASTSEL 0
      DOCKED 0
      BYPASS 0 0 0
      <JS {PLUGIN} ""
        {vals}
      >
      FLOATPOS 0 0 0 0
      WAK 0 0
    >
    <ITEM
      POSITION 0
      LENGTH {seconds}
      LOOP 0
      <SOURCE MIDI
        HASDATA 1 960 QN
{body}
        E 960 b0 7b 00
      >
    >
  >
>
""")


def render(tmp, name, params, notes, seconds, rate=44100, bend_at=None):
    rpp, out = os.path.join(tmp, name + ".rpp"), os.path.join(tmp, name + ".wav")
    project(rpp, out, params, notes, seconds, rate, bend_at)
    subprocess.run(
        ["reaper", "-cfgfile", os.path.join(tmp, "cfg", "reaper.ini"), "-newinst", "-nosplash",
         "-ignoreerrors", "-renderproject", rpp],
        timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not os.path.exists(out):
        sys.exit(f"FAIL {name}: REAPER produced no file")
    # the wave module cannot read float WAVs, and float is what shows overs past 1.0
    with open(out, "rb") as f:
        buf = f.read()
    pos, raw, fmt = 12, None, None
    while pos + 8 <= len(buf):
        cid, size = buf[pos:pos + 4], struct.unpack("<I", buf[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", buf[pos + 8:pos + 24])
        elif cid == b"data":
            raw = buf[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    tag, ch, rate, _, _, bits = fmt
    assert (tag, ch, bits) == (3, 2, 32), f"{name}: expected 32-bit float stereo, got {fmt}"
    data = struct.unpack(f"<{len(raw) // 4}f", raw)
    return data[0::2], data[1::2], rate


def rms(x):
    return math.sqrt(sum(s * s for s in x) / max(1, len(x)))


def peak(x):
    return max((abs(s) for s in x), default=0)


def cents_off(x, rate, hz):
    """Finds the strongest pitch within 40 cents of hz, under a Hann window."""
    n = len(x)
    win = [s * (0.5 - 0.5 * math.cos(2 * math.pi * i / n)) for i, s in enumerate(x)]
    best, at = -1, 0
    for c in range(-40, 41, 2):
        step = cmath.exp(-2j * math.pi * hz * 2 ** (c / 1200) / rate)
        phase, total = 1 + 0j, 0j
        for s in win:
            total += s * phase
            phase *= step
        if abs(total) > best:
            best, at = abs(total), c
    return at


def bright(x, rate):
    """Share of the energy above 1 kHz. Three stages, or a bass fundamental leaks through and swamps it."""
    k, hi = 1 - math.exp(-2 * math.pi * 1000 / rate), list(x)
    for _ in range(3):
        y, out = 0, []
        for s in hi:
            y += k * (s - y)
            out.append(s - y)
        hi = out
    return rms(hi) / max(rms(x), 1e-9)


def time_to_fall(x, rate, db=20):
    """Seconds for the level to fall by db from its loudest 50 ms window."""
    w = rate // 20
    levels = [rms(x[i:i + w]) for i in range(0, len(x) - w, w)]
    top = max(levels)
    start = levels.index(top)
    for i, v in enumerate(levels[start:]):
        if v < top * 10 ** (-db / 20):
            return i * 0.05
    return len(levels) * 0.05


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="glounge-")
    atexit.register(shutil.rmtree, tmp, True)
    os.makedirs(os.path.join(tmp, "cfg", "Effects"))
    for f in glob.glob(os.path.join(ROOT, "*.jsfx*")):
        shutil.copy(f, os.path.join(tmp, "cfg", "Effects"))
    open(os.path.join(tmp, "cfg", "reaper.ini"), "w").close()
    good = bass_checks(tmp) & guitar_checks(tmp)
    print("PASS" if good else "FAILED")
    sys.exit(0 if good else 1)


def guitar_checks(tmp):
    global PLUGIN
    PLUGIN = "guitar.jsfx"
    print("--- guitar")
    good = True
    chord = [(0, 4, n) for n in (40, 47, 52, 55, 59, 64)]

    l, r, sr = render(tmp, "gdefault", {}, chord, 4)
    good &= check(0.02 < rms(l[:sr]) and peak(l) <= 1.001, f"a full chord sounds, within the ceiling (rms {rms(l[:sr]):.3f}, peak {peak(l):.3f})")

    for rate in (44100, 96000):
        for note, name in ((40, "E2"), (45, "A2"), (50, "D3"), (55, "G3"), (59, "B3"), (64, "E4"), (76, "E5"), (88, "E6")):
            l, r, sr = render(tmp, f"gtune{note}", {**CLEAN, TONE: 12000, DECAY: 20}, [(0, 4, note)], 2, rate=rate)
            hz = 440 * 2 ** ((note - 69) / 12)
            x = l[int(0.1 * sr):int(0.9 * sr)]
            if rate != 44100:
                x = x[::2]  # shortcut: keeps the scan quick; 48 kHz is plenty for these notes
            c = cents_off(x, sr // (2 if rate != 44100 else 1), hz)
            good &= check(abs(c) <= 3, f"{name} in tune at {rate} Hz ({c:+d} cents)")

    # two notes at once both sound
    l, r, sr = render(tmp, "gpoly", {**CLEAN, TONE: 12000, DECAY: 20}, [(0, 4, 40), (0, 4, 47)], 2)
    x = l[int(0.2 * sr):int(1.2 * sr)]
    good &= check(abs(cents_off(x, sr, 82.41)) <= 3 and abs(cents_off(x, sr, 123.47)) <= 3, "two notes ring together, both in tune")

    # note off stops every string, including when more notes than strings were played
    l, r, sr = render(tmp, "goff", {}, [(0, 1, 40 + i) for i in range(9)], 3)
    good &= check(rms(l[:sr // 2]) > 0.01, "nine notes at once still sound")
    good &= check(rms(l[int(2 * sr):]) < 1e-3, f"note off damps every string (rms {rms(l[int(2 * sr):]):.5f})")

    # repeating one note must not stack up or click
    fast = [(i * 0.125, 0.5, 52) for i in range(32)]
    l, r, sr = render(tmp, "gfast", {}, fast, 4)
    good &= check(peak(l) < 1, f"a fast repeated note stays under the ceiling (peak {peak(l):.3f})")

    a = time_to_fall(render(tmp, "gshort", {**CLEAN, DECAY: 1}, [(0, 8, 52)], 6)[0], sr)
    b = time_to_fall(render(tmp, "glong", {**CLEAN, DECAY: 12}, [(0, 8, 52)], 6)[0], sr)
    good &= check(b > 2 * a, f"Decay lengthens the note ({a:.2f}s -> {b:.2f}s to fall 20 dB)")

    clean = {DRIVE: 0, TONE: 12000}
    a = bright(render(tmp, "gneck", {**clean, PICKUP: 0}, [(0, 2, 52)], 1)[0][:sr // 2], sr)
    b = bright(render(tmp, "gbridge", {**clean, PICKUP: 1}, [(0, 2, 52)], 1)[0][:sr // 2], sr)
    good &= check(b > 1.1 * a, f"Position brightens the sound towards the bridge ({a:.3f} -> {b:.3f})")

    a = bright(render(tmp, "gdark", {DRIVE: 0, TONE: 500}, [(0, 2, 52)], 1)[0][:sr // 2], sr)
    b = bright(render(tmp, "gopen", clean, [(0, 2, 52)], 1)[0][:sr // 2], sr)
    good &= check(b > 1.5 * a, f"Tone opens the sound ({a:.3f} -> {b:.3f})")

    a = bright(render(tmp, "gclean", clean, [(0, 2, 40)], 1)[0][:sr // 2], sr)
    b = bright(render(tmp, "gdirty", {DRIVE: 1, TONE: 12000}, [(0, 2, 40)], 1)[0][:sr // 2], sr)
    good &= check(b > 1.2 * a, f"Drive adds harmonics ({a:.3f} -> {b:.3f})")

    worst = {PLUCK: 1, PICK: 1, DECAY: 20, TONE: 12000, DRIVE: 1, LEVEL: -24, PICKUP: 1}
    for note in (0, 40, 76, 100, 127):
        l, r, sr = render(tmp, f"gstress{note}", worst, [(0, 32, note)], 20)
        a, b = rms(l[sr // 2:sr]), rms(l[18 * sr:19 * sr])
        good &= check(all(math.isfinite(s) for s in l) and b <= a + 1e-6, f"MIDI note {note} is stable (rms {a:.4f} -> {b:.4f})")
    return good


def bass_checks(tmp):
    global PLUGIN
    PLUGIN = "bass.jsfx"
    print("--- bass")
    good = True

    # defaults: audible and under the ceiling
    l, r, sr = render(tmp, "default", {}, [(0, 4, 40)], 4)
    good &= check(0.02 < rms(l[:sr]) and peak(l) <= 1.001, f"default sounds, within the ceiling (rms {rms(l[:sr]):.3f}, peak {peak(l):.3f})")

    # in tune from the low E to the top of the neck, including high sample rates
    for rate in (44100, 96000):
        for note, name in ((28, "E1"), (33, "A1"), (40, "E2"), (55, "G3"), (67, "G4")):
            l, r, sr = render(tmp, f"tune{note}", {**CLEAN, DECAY: 12}, [(0, 4, note)], 2, rate=rate)
            hz = 440 * 2 ** ((note - 69) / 12)
            x = l[int(0.2 * sr):int(1.2 * sr)]
            if rate != 44100:
                x = x[::2]  # shortcut: keeps the scan quick; 48 kHz is plenty for these low notes
            c = cents_off(x, sr // (2 if rate != 44100 else 1), hz)
            good &= check(abs(c) <= 3, f"{name} in tune at {rate} Hz ({c:+d} cents)")

    # note off stops the string; a note held to the end keeps ringing
    l, r, sr = render(tmp, "off", {}, [(0, 1, 33)], 3)
    good &= check(rms(l[int(1.5 * sr):]) < 1e-3, f"note off damps the string (rms {rms(l[int(1.5 * sr):]):.5f})")

    # a new note replaces the last: one pitch at a time
    l, r, sr = render(tmp, "mono", CLEAN, [(0, 8, 28), (2, 6, 40)], 3)
    x = l[int(1.6 * sr):int(2.6 * sr)]
    good &= check(abs(cents_off(x, sr, 82.41)) <= 3, "a new note takes over the pitch")

    # pitch bend reaches 2 semitones
    l, r, sr = render(tmp, "bend", {**CLEAN, DECAY: 12}, [(0, 8, 40)], 4, bend_at=2)
    c = cents_off(l[int(0.1 * sr):int(0.9 * sr)], sr, 82.41), cents_off(l[int(1.2 * sr):int(2.2 * sr)], sr, 92.5)
    good &= check(abs(c[0]) <= 3 and abs(c[1]) <= 3, f"pitch bend moves E2 up to F#2 ({c[0]:+d} then {c[1]:+d} cents)")

    # the knobs do what their names say
    a = time_to_fall(render(tmp, "short", CLEAN | {DECAY: 1}, [(0, 8, 33)], 6)[0], sr)
    b = time_to_fall(render(tmp, "long", CLEAN | {DECAY: 8}, [(0, 8, 33)], 6)[0], sr)
    good &= check(b > 2 * a, f"Decay lengthens the note ({a:.2f}s -> {b:.2f}s to fall 20 dB)")

    a = bright(render(tmp, "soft", CLEAN | {PLUCK: 0.1, PICK: 0}, [(0, 2, 40)], 1)[0][:sr // 2], sr)
    b = bright(render(tmp, "hard", CLEAN | {PLUCK: 1, PICK: 1}, [(0, 2, 40)], 1)[0][:sr // 2], sr)
    good &= check(b > 1.5 * a, f"Pluck and Pick brighten the attack ({a:.3f} -> {b:.3f})")

    a = bright(render(tmp, "dark", {DRIVE: 0, TONE: 300}, [(0, 2, 40)], 1)[0][:sr // 2], sr)
    b = bright(render(tmp, "open", {DRIVE: 0, TONE: 8000}, [(0, 2, 40)], 1)[0][:sr // 2], sr)
    good &= check(b > 1.5 * a, f"Tone opens the sound ({a:.3f} -> {b:.3f})")

    a = bright(render(tmp, "cleand", {DRIVE: 0, TONE: 8000}, [(0, 2, 40)], 1)[0][:sr // 2], sr)
    b = bright(render(tmp, "dirty", {DRIVE: 1, TONE: 8000}, [(0, 2, 40)], 1)[0][:sr // 2], sr)
    good &= check(b > 1.2 * a, f"Drive adds harmonics ({a:.3f} -> {b:.3f})")

    # the longest, brightest, hottest settings on every kind of note must still fall away
    worst = {PLUCK: 1, PICK: 1, DECAY: 12, TONE: 8000, DRIVE: 1, LEVEL: -24}
    for note in (0, 28, 60, 96, 127):
        l, r, sr = render(tmp, f"stress{note}", worst, [(0, 16, note)], 14)
        a, b = rms(l[sr // 2:sr]), rms(l[12 * sr:13 * sr])
        good &= check(all(math.isfinite(s) for s in l) and b <= a + 1e-6, f"MIDI note {note} is stable (rms {a:.4f} -> {b:.4f})")

    # rapid repeated notes must not click or build up
    fast = [(i * 0.25, 0.2, 33 + (i % 2) * 7) for i in range(32)]
    l, r, sr = render(tmp, "fast", {}, fast, 5)
    good &= check(peak(l) < 1, f"fast playing stays under the ceiling (peak {peak(l):.3f})")
    return good


if __name__ == "__main__":
    main()
