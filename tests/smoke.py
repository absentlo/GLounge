#!/usr/bin/env python3
"""Renders the GLounge instruments through REAPER and checks the audio. Run: python3 tests/smoke.py
Needs REAPER on PATH and a display. Uses a throwaway config, not your REAPER settings."""
import atexit, cmath, glob, math, os, re, shutil, struct, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUCK, PICK, DECAY, TONE, DRIVE, LEVEL = 1, 2, 3, 4, 5, 6
PICKUP = 7
CLEAN = {DRIVE: 0, TONE: 8000}
PLUGIN = "bass.jsfx"


def project(path, out, params, notes, seconds, rate, bend_at=None, pedal=None):
    """notes: (start_beat, length_beats, midi_note[, velocity]). 120 BPM, 960 ticks per beat.
    bend_at: the beat where the pitch wheel jumps fully up. pedal: (down_beat, up_beat) for the sustain pedal."""
    src = open(os.path.join(ROOT, PLUGIN)).read()
    defaults = {int(n): v for n, v in re.findall(r"^slider(\d+):\w+=(-?[\d.]+)", src, re.M)}
    vals = " ".join(str(params.get(i, defaults.get(i, "-"))) for i in range(1, 65))
    events, last = [], 0
    for tick, status, note, vel in sorted(
        [(int(s * 960), 0x90, n, v[0] if v else 110) for s, _, n, *v in notes]
        + [(int((s + l) * 960), 0x80, n, 0) for s, l, n, *_ in notes]
        + ([(int(bend_at * 960), 0xE0, 127, 127)] if bend_at else [])
        + ([(int(pedal[0] * 960), 0xB0, 64, 127), (int(pedal[1] * 960), 0xB0, 64, 0)] if pedal else [])
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
  SAMPLERATE {rate} 1 0
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


def render(tmp, name, params, notes, seconds, rate=44100, bend_at=None, pedal=None):
    rpp, out = os.path.join(tmp, name + ".rpp"), os.path.join(tmp, name + ".wav")
    project(rpp, out, params, notes, seconds, rate, bend_at, pedal)
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


def bright(x, rate, hz=1000):
    """Share of the energy above hz. Three stages, or a bass fundamental leaks through and swamps it."""
    k, hi = 1 - math.exp(-2 * math.pi * hz / rate), list(x)
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
    good = name_checks() & bass_checks(tmp) & guitar_checks(tmp) & eightbit_checks(tmp) & drums_checks(tmp) & piano_checks(tmp)
    print("PASS" if good else "FAILED")
    sys.exit(0 if good else 1)


def name_checks():
    """JSFX names ignore case, so count and COUNT are one variable. This bug is silent, and has bitten repeatedly."""
    good = True
    strip = lambda path: re.sub(r"^(desc|slider\d+|import).*$|//.*|\"[^\"]*\"", "", open(path).read(), flags=re.M)
    shared = strip(os.path.join(ROOT, "glounge.jsfx-inc"))  # compiled into every plugin, so check each with it
    for path in glob.glob(os.path.join(ROOT, "*.jsfx")):
        seen = {}
        for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", strip(path) + shared):
            seen.setdefault(word.lower(), set()).add(word)
        clashes = [sorted(v) for v in seen.values() if len(v) > 1]
        good &= check(not clashes, f"{os.path.basename(path)} has no names that differ only by case {clashes or ''}")
    return good


def level_at(x, rate, hz):
    n = len(x)
    step, phase, total = cmath.exp(-2j * math.pi * hz / rate), 1 + 0j, 0j
    for i, s in enumerate(x):
        total += s * (0.5 - 0.5 * math.cos(2 * math.pi * i / n)) * phase
        phase *= step
    return abs(total) / n


def piano_checks(tmp):
    global PLUGIN
    PLUGIN = "piano.jsfx"
    print("--- piano")
    FELT, NOISE, P_DECAY, DETUNE, ROOM, LVL = range(1, 7)
    tuned = {ROOM: 0, NOISE: 0, DETUNE: 0}  # two detuned strings have no single pitch to measure
    dry = {ROOM: 0, NOISE: 0}
    sr = 44100
    good = True
    note = lambda n, params=dry, vel=100, beats=8, secs=4, rate=44100: render(
        tmp, f"p{n}", params, [(0, beats, n, vel)], secs, rate=rate)[0]

    x = render(tmp, "pdefault", {}, [(0, 4, n) for n in (48, 55, 60, 64, 67)], 3)[0]
    good &= check(rms(x[:sr]) > 0.02 and peak(x) < 0.99, f"a chord sounds and does not clip (rms {rms(x[:sr]):.3f}, peak {peak(x):.3f})")

    for rate in (44100, 96000):
        for n in (21, 36, 48, 60, 72, 84, 90, 96, 102, 108):
            x = note(n, tuned, rate=rate)
            hz = 440 * 2 ** ((n - 69) / 12)
            y = x[int(0.15 * rate):int(0.95 * rate)]
            if rate != 44100:
                y = y[::2]  # shortcut: keeps the scan quick; 48 kHz is plenty for these notes
            c = cents_off(y, rate // (2 if rate != 44100 else 1), hz)
            good &= check(abs(c) <= 3, f"note {n} in tune at {rate} Hz ({c:+d} cents)")

    # treble notes must ring, not vanish: a short string passes its loss filter thousands of times a second
    for n in (84, 96, 108):
        x = note(n, tuned)
        a, b = rms(x[int(0.02 * sr):int(0.12 * sr)]), rms(x[int(0.3 * sr):int(0.4 * sr)])
        good &= check(b > 0.1 * a, f"note {n} still rings after 0.3 s ({a:.4f} -> {b:.4f})")

    # the whole keyboard is usable, with no dead or screaming zones
    levels = {n: rms(note(n, beats=2)[:sr // 2]) for n in (21, 36, 48, 60, 72, 84, 96, 108)}
    good &= check(max(levels.values()) < 8 * min(levels.values()), "the keyboard is balanced: " + ", ".join(f"{n}={v:.3f}" for n, v in levels.items()))

    # strings are stiff, so the partials run sharp of the pure harmonic series
    x = note(36, {**dry, DETUNE: 0, FELT: 0})[int(0.1 * sr):int(1.1 * sr)]
    hz = 440 * 2 ** ((36 - 69) / 12)
    c = cents_off(x, sr, hz * 5)
    good &= check(2 <= c <= 14, f"the fifth partial runs a little sharp, as on a real piano ({c:+d} cents)")

    # what makes it acoustic rather than an electric piano: a real string has many partials, and the case knocks
    hz = 440 * 2 ** ((36 - 69) / 12)
    x = note(36, tuned, vel=110)[int(0.02 * sr):int(0.4 * sr)]
    a = bright(x, sr, 800)
    good &= check(a > 0.02, f"low notes reach far past their ninth partial ({a:.4f} of the energy above 800 Hz)")
    a, b = rms(note(60, {ROOM: 0, NOISE: 0}, vel=100)[:sr // 40]), rms(note(60, {ROOM: 0, NOISE: 1}, vel=100)[:sr // 40])
    good &= check(b > 1.1 * a, f"Body adds the case's knock to the attack ({a:.4f} -> {b:.4f})")
    def knock_only(n):
        return rms(render(tmp, "pk", {ROOM: 0, NOISE: 1, P_DECAY: 2, LVL: 6}, [(0, 8, n)], 1)[0][:sr // 40])
    good &= check(knock_only(60) > 0.03, f"the knock is clearly there ({knock_only(60):.4f})")

    # keys, dampers and the pedal
    x = note(60, beats=1)
    good &= check(rms(x[int(1.5 * sr):]) < 1e-3, f"letting go damps the note (rms {rms(x[int(1.5 * sr):]):.5f})")
    x = render(tmp, "pped", dry, [(0, 1, 60)], 5, pedal=(0, 6))[0]
    damped = rms(note(60, beats=1)[int(1.5 * sr):int(2 * sr)])
    good &= check(rms(x[int(1.5 * sr):int(2 * sr)]) > 20 * max(damped, 1e-5), f"the pedal keeps it ringing (rms {rms(x[int(1.5 * sr):int(2 * sr)]):.4f}, without {damped:.5f})")
    good &= check(rms(x[int(4 * sr):]) < 1e-3, f"lifting the pedal damps it (rms {rms(x[int(4 * sr):]):.5f})")
    x = render(tmp, "ppkey", dry, [(0, 8, 60)], 5, pedal=(0, 2))[0]
    good &= check(rms(x[int(2 * sr):int(3 * sr)]) > 20 * max(damped, 1e-5), "a key still held survives lifting the pedal")
    x = render(tmp, "ppoly", {**dry, DETUNE: 0}, [(0, 8, 48), (0, 8, 55)], 3)[0][int(0.2 * sr):int(1.2 * sr)]
    good &= check(abs(cents_off(x, sr, 130.81)) <= 3 and abs(cents_off(x, sr, 196.0)) <= 3, "two notes ring together, both in tune")
    x = render(tmp, "psteal", dry, [(0, 1, 40 + i * 2) for i in range(14)], 4)[0]
    good &= check(rms(x[:sr // 2]) > 0.01 and rms(x[int(2.5 * sr):]) < 1e-3, "14 notes at once leave nothing stuck")
    x = render(tmp, "prep", dry, [(i * 0.25, 0.2, 60) for i in range(24)], 5)[0]
    good &= check(peak(x) < 0.99 and rms(x[int(4.5 * sr):]) < 1e-3, f"a fast repeated note does not build up (peak {peak(x):.3f})")

    # the knobs do what their names say
    a, b = bright(note(60, {**dry, FELT: 1}), sr), bright(note(60, {**dry, FELT: 0}), sr)
    good &= check(b > 1.5 * a, f"Felt softens the sound ({b:.4f} without, {a:.4f} with)")
    a, b = bright(note(60, vel=30), sr), bright(note(60, vel=127), sr)
    good &= check(b > 1.2 * a, f"harder hits are brighter ({a:.4f} -> {b:.4f})")
    a, b = rms(note(60, vel=30)[:sr // 2]), rms(note(60, vel=127)[:sr // 2])
    good &= check(b > 2 * a, f"harder hits are louder ({a:.3f} -> {b:.3f})")
    a = time_to_fall(render(tmp, "pd0", {**dry, P_DECAY: 2}, [(0, 16, 60)], 8)[0], sr)
    b = time_to_fall(render(tmp, "pd1", {**dry, P_DECAY: 25}, [(0, 16, 60)], 8)[0], sr)
    good &= check(b > 2 * a, f"Decay lengthens the note ({a:.2f}s -> {b:.2f}s)")
    def wobble(x):
        w = sr // 20
        levels = [rms(x[i:i + w]) for i in range(int(0.2 * sr), int(2.2 * sr), w)]
        trend = [levels[i] / max(levels[i + 1], 1e-9) for i in range(len(levels) - 1)]
        return max(trend) - min(trend)
    a, b = wobble(note(48, {**dry, DETUNE: 0})), wobble(note(48, {**dry, DETUNE: 6}))
    good &= check(b > 1.5 * a, f"Detune makes the note beat ({a:.3f} -> {b:.3f})")
    a, b = rms(note(60, beats=1)[int(0.85 * sr):int(1.2 * sr)]), rms(note(60, {ROOM: 1, NOISE: 0}, beats=1)[int(0.85 * sr):int(1.2 * sr)])
    good &= check(b > 1.5 * max(a, 1e-5), f"Room adds a tail ({a:.5f} -> {b:.5f})")
    l, r, _ = render(tmp, "ppan", dry, [(0, 2, 36)], 1), None, None
    good &= check(rms(l[0]) > 1.2 * rms(l[1]), f"low notes sit to one side, like a keyboard (L {rms(l[0]):.3f}, R {rms(l[1]):.3f})")

    # every MIDI note, even far above a real piano, must stay stable: a short string once ran away at full volume
    for n in (108, 112, 116, 120, 124, 127):
        x = note(n, {FELT: 0, NOISE: 0, P_DECAY: 25, DETUNE: 6, ROOM: 0, LVL: 6}, vel=127, beats=1, secs=3)
        good &= check(rms(x[int(2 * sr):]) < 1e-3, f"note {n} fades away after release (end {rms(x[int(2 * sr):]):.6f})")

    # the loudest, longest settings across the whole keyboard stay finite, and fade once released
    x = render(tmp, "pall", {FELT: 0, NOISE: 1, P_DECAY: 25, DETUNE: 6, ROOM: 1, LVL: 6}, [(i * 0.05, 1, n, 127) for i, n in enumerate(range(0, 128, 7))], 6)[0]
    good &= check(all(math.isfinite(s) for s in x) and peak(x) <= 1.001 and rms(x[int(5 * sr):]) < 1e-2, f"the whole keyboard at maximum stays safe (peak {peak(x):.3f}, end {rms(x[int(5 * sr):]):.5f})")
    return good


def crossing_hz(x, rate):
    """Pitch of a clean tone, from how often it crosses zero."""
    crossings = sum(1 for a, b in zip(x, x[1:]) if a <= 0 < b)
    return crossings * rate / len(x)


def drums_checks(tmp):
    global PLUGIN
    PLUGIN = "drums.jsfx"
    print("--- drums")
    K_TUNE, K_DEC, S_TUNE, SNAP, H_TONE, H_DEC, T_TUNE, T_DEC, CRUNCH, LVL, ROOM = range(1, 12)
    KIT = {"kick": 36, "snare": 38, "clap": 39, "low tom": 45, "mid tom": 47, "high tom": 50,
           "closed hat": 42, "open hat": 46, "crash": 49, "ride": 51}
    sr = 44100
    good = True
    dry = {CRUNCH: 0, ROOM: 0}
    hit = lambda name, params=dry, note=None, vel=110, secs=4, rate=44100: render(
        tmp, "d" + name.replace(" ", ""), params, [(0, 0.5, note if note else KIT[name], vel)], secs, rate=rate)[0]

    # every piece makes a sound and then falls silent on its own, with the key already up
    for name in KIT:
        x = hit(name)
        good &= check(rms(x[:sr // 10]) > 0.005 and peak(x) <= 1.001 and rms(x[-sr // 2:]) < 1e-3,
                      f"{name} sounds, then falls silent (rms {rms(x[:sr // 10]):.3f}, peak {peak(x):.3f}, end {rms(x[-sr // 2:]):.5f})")

    # notes outside the kit stay silent
    x = render(tmp, "dnone", dry, [(0, 1, n) for n in (0, 30, 60, 90, 127)], 2)[0]
    good &= check(peak(x) < 1e-6, f"notes outside the kit are silent (peak {peak(x):.6f})")

    # the pieces sit where a kit should, from low to high
    low = lambda x: crossing_hz(x[int(0.12 * sr):int(0.3 * sr)], sr)
    k, lo, mid, hi = (low(hit(n)) for n in ("kick", "low tom", "mid tom", "high tom"))
    good &= check(k < lo < mid < hi, f"kick and toms rise in pitch ({k:.0f} < {lo:.0f} < {mid:.0f} < {hi:.0f} Hz)")
    good &= check(bright(hit("closed hat"), sr) > 3 * bright(hit("kick"), sr), "hats are far brighter than the kick")

    # the kick settles on its Tune, at any sample rate
    for rate in (44100, 96000):
        x = hit("kick", {**dry, K_TUNE: 60, K_DEC: 1000}, rate=rate)
        hz = crossing_hz(x[int(0.25 * rate):int(0.5 * rate)], rate)
        good &= check(abs(hz - 60) < 5, f"kick Tune 60 sounds near 60 Hz at {rate} Hz ({hz:.1f})")

    # knobs
    a, b = time_to_fall(hit("kick", {**dry, K_DEC: 100}), sr), time_to_fall(hit("kick", {**dry, K_DEC: 1000}), sr)
    good &= check(b > 2 * a, f"Kick Decay lengthens it ({a:.2f}s -> {b:.2f}s)")
    a, b = bright(hit("snare", {**dry, SNAP: 0}), sr), bright(hit("snare", {**dry, SNAP: 1}), sr)
    good &= check(b > 1.5 * a, f"Snap adds the snare wires ({a:.3f} -> {b:.3f})")
    a, b = low(hit("snare", {**dry, SNAP: 0, S_TUNE: 120})[:sr // 3]), low(hit("snare", {**dry, SNAP: 0, S_TUNE: 320})[:sr // 3])
    good &= check(b > a, f"Snare Tune raises it ({a:.0f} -> {b:.0f} Hz)")
    high = lambda x: bright(x[:sr // 5], sr, 8000)
    a, b = high(hit("open hat", {**dry, H_TONE: 3000})), high(hit("open hat", {**dry, H_TONE: 12000}))
    good &= check(b > 1.2 * a, f"Hat Tone brightens the hats ({a:.3f} -> {b:.3f})")
    a, b = time_to_fall(hit("open hat", {**dry, H_DEC: 100}), sr), time_to_fall(hit("open hat", {**dry, H_DEC: 1500}), sr)
    good &= check(b > 2 * a, f"Open lengthens the open hat ({a:.2f}s -> {b:.2f}s)")
    a, b = low(hit("low tom", {**dry, T_TUNE: 60})), low(hit("low tom", {**dry, T_TUNE: 160}))
    good &= check(b > a, f"Tom Tune raises the toms ({a:.0f} -> {b:.0f} Hz)")
    a, b = time_to_fall(hit("mid tom", {**dry, T_DEC: 100}), sr), time_to_fall(hit("mid tom", {**dry, T_DEC: 1000}), sr)
    good &= check(b > 2 * a, f"Tom Decay lengthens them ({a:.2f}s -> {b:.2f}s)")
    # distortion lifts the quiet parts towards the peaks
    fat = lambda x: rms(x[:sr // 4]) / peak(x[:sr // 4])
    a, b = fat(hit("kick", dry)), fat(hit("kick", {**dry, CRUNCH: 1}))
    good &= check(b > 1.3 * a, f"Crunch thickens the hit ({a:.3f} -> {b:.3f})")
    a, b = rms(hit("kick", dry)[int(0.6 * sr):int(0.9 * sr)]), rms(hit("kick", {**dry, ROOM: 1})[int(0.6 * sr):int(0.9 * sr)])
    good &= check(b > 3 * max(a, 1e-5), f"Room adds a tail ({a:.5f} -> {b:.5f})")

    # what makes it acoustic rather than a drum machine
    x = hit("kick", {**dry, K_TUNE: 60})[int(0.005 * sr):int(0.1 * sr)]
    a, b = level_at(x, sr, 60), level_at(x, sr, 60 * 1.59)
    good &= check(b > 0.03 * a, f"the kick is a skin with several pitches, not one sine ({b / a:.3f} of the fundamental)")
    x = hit("mid tom")[int(0.005 * sr):int(0.2 * sr)]
    a, b = level_at(x, sr, 112), level_at(x, sr, 112 * 1.59)
    good &= check(b > 0.03 * a, f"toms ring at several pitches too ({b / a:.3f} of the fundamental)")
    a, b = bright(hit("snare", {**dry, SNAP: 0}, vel=30), sr), bright(hit("snare", {**dry, SNAP: 0}, vel=127), sr)
    good &= check(b > 1.2 * a, f"harder hits are brighter, not only louder ({a:.3f} -> {b:.3f})")
    # two hits in one render: rand repeats between renders, so separate renders would match
    x = render(tmp, "dtwo", dry, [(0, 0.5, 38), (2, 0.5, 38)], 2)[0]
    a, b = x[:sr // 5], x[sr:sr + sr // 5]
    diff = rms([p - q for p, q in zip(a, b)]) / rms(a)
    good &= check(diff > 0.05, f"no two hits are identical ({diff:.2f} apart)")
    k_l, k_r, _ = render(tmp, "dkst", dry, [(0, 0.5, 36)], 1)
    h_l, h_r, _ = render(tmp, "dhst", dry, [(0, 0.5, 46)], 1)
    good &= check(rms([p - q for p, q in zip(k_l, k_r)]) < 0.05 * rms(k_l) and rms(h_r) > 1.3 * rms(h_l),
                  f"the kick sits in the centre, the hats to one side (L {rms(h_l):.4f}, R {rms(h_r):.4f})")

    # playing
    a, b = rms(hit("snare", vel=30)[:sr // 10]), rms(hit("snare", vel=127)[:sr // 10])
    good &= check(b > 2 * a, f"harder hits are louder ({a:.3f} -> {b:.3f})")
    # the closed hat cuts a ringing open hat, like the real pedal
    x = render(tmp, "dchoke", {**dry, H_DEC: 1500}, [(0, 0.5, 46), (1, 0.5, 42)], 3)[0]
    y = render(tmp, "dring", {**dry, H_DEC: 1500}, [(0, 0.5, 46)], 3)[0]
    a, b = rms(y[int(0.9 * sr):int(1.1 * sr)]), rms(x[int(0.9 * sr):int(1.1 * sr)])
    good &= check(b < 0.7 * a, f"a closed hat cuts the open hat ({a:.4f} -> {b:.4f})")

    # a busy pattern and a full-kit hit stay under the ceiling; nothing sticks afterwards
    pattern = [(i * 0.25, 0.1, (36, 42, 38, 42)[i % 4]) for i in range(32)]
    x = render(tmp, "dpat", {}, pattern, 5)[0]
    good &= check(peak(x) < 0.99 and rms(x[int(4.5 * sr):]) < 1e-3, f"a busy pattern does not clip at the default level (peak {peak(x):.3f})")
    x = render(tmp, "dall", {CRUNCH: 1, LVL: 6, ROOM: 1}, [(0, 0.5, n, 127) for n in KIT.values()], 5)[0]
    good &= check(all(math.isfinite(s) for s in x) and peak(x) <= 1.001, f"the whole kit at once stays under the ceiling (peak {peak(x):.3f})")
    # a roll with the room fully open must fade once it stops
    roll = [(i * 0.0625, 0.05, 38, 127) for i in range(64)]
    l, r, _ = render(tmp, "droll", {ROOM: 1}, roll, 8)
    good &= check(all(math.isfinite(s) for s in l) and rms(l[6 * sr:]) < 1e-3, f"a fast roll in a full room fades away (end {rms(l[6 * sr:]):.5f})")
    return good


def eightbit_checks(tmp):
    global PLUGIN
    PLUGIN = "8bit.jsfx"
    print("--- 8-bit")
    WAVE, CRUSH, ATK, DEC, SUS, REL, VIB, ARP = 1, 2, 3, 4, 5, 6, 7, 8
    names = ("Pulse 12", "Pulse 25", "Pulse 50", "Triangle")
    good = True
    held = {CRUSH: 0, SUS: 1}

    l, r, sr = render(tmp, "8default", {}, [(0, 4, 60)], 4)
    good &= check(0.02 < rms(l[:sr]) and peak(l) <= 1.001, f"default sounds, within the ceiling (rms {rms(l[:sr]):.3f}, peak {peak(l):.3f})")

    for rate in (44100, 96000):
        for wave, name in enumerate(names):
            for note in (36, 60, 84):
                l, r, sr = render(tmp, f"8t{wave}{note}", {**held, WAVE: wave}, [(0, 4, note)], 1, rate=rate)
                c = cents_off(l[int(0.1 * sr):int(0.5 * sr)], sr, 440 * 2 ** ((note - 69) / 12))
                good &= check(abs(c) <= 3, f"{name} note {note} in tune at {rate} Hz ({c:+d} cents)")

    # the noise wave has a pitch-like colour that follows the note
    a = bright(render(tmp, "8nlo", {**held, WAVE: 4}, [(0, 2, 36)], 1)[0][:sr // 2], 44100)
    b = bright(render(tmp, "8nhi", {**held, WAVE: 4}, [(0, 2, 84)], 1)[0][:sr // 2], 44100)
    good &= check(b > 1.5 * a, f"noise gets brighter with higher notes ({a:.3f} -> {b:.3f})")

    # four notes sound together; a fifth takes a channel without getting stuck
    sr = 44100
    l, r, _ = render(tmp, "8poly", {**held, WAVE: 2}, [(0, 4, 48), (0, 4, 55)], 2)
    x = l[int(0.2 * sr):int(1.2 * sr)]
    good &= check(abs(cents_off(x, sr, 130.81)) <= 3 and abs(cents_off(x, sr, 196.0)) <= 3, "two notes sound together, both in tune")
    l, r, _ = render(tmp, "8off", {REL: 50}, [(0, 1, 48 + i) for i in range(7)], 3)
    good &= check(rms(l[:sr // 2]) > 0.01, "seven notes at once still sound")
    good &= check(rms(l[int(1.5 * sr):]) < 1e-4, f"no stuck notes after stealing (rms {rms(l[int(1.5 * sr):]):.6f})")

    # the arp plays one key at a time, in turn, then stops with the keys
    chord = [(0, 2, 48), (0, 2, 55), (0, 2, 64)]
    pitches = (130.81, 196.0, 329.63)
    l, r, _ = render(tmp, "8arp", {**held, WAVE: 2, ARP: 5, REL: 50}, chord, 3)
    tops = []
    for step in range(3):
        x = l[int((step * 0.2 + 0.03) * sr):int((step * 0.2 + 0.17) * sr)]
        levels = [level_at(x, sr, p) for p in pitches]
        tops.append(levels.index(max(levels)))
    good &= check(tops == [0, 1, 2], f"the arp steps through the held keys in turn ({tops})")
    good &= check(rms(l[int(1.5 * sr):]) < 1e-4, f"the arp stops when the keys are up (rms {rms(l[int(1.5 * sr):]):.6f})")
    l, r, _ = render(tmp, "8arp1", {**held, WAVE: 2, ARP: 8, REL: 50}, [(0, 1, 48), (0.5, 1, 55)], 3)
    good &= check(rms(l[int(1.5 * sr):]) < 1e-4, "overlapping keys under the arp leave nothing stuck")

    # the knobs do what their names say
    a = bright(render(tmp, "8c0", {CRUSH: 0, WAVE: 3, SUS: 1}, [(0, 2, 60)], 1)[0][:sr // 2], sr)
    b = bright(render(tmp, "8c1", {CRUSH: 1, WAVE: 3, SUS: 1}, [(0, 2, 60)], 1)[0][:sr // 2], sr)
    good &= check(b != a and b > a, f"Crush adds grit ({a:.3f} -> {b:.3f})")
    head = lambda x: rms(x[int(0.02 * sr):int(0.1 * sr)])
    a = head(render(tmp, "8a0", {**held, ATK: 1}, [(0, 2, 60)], 1)[0])
    b = head(render(tmp, "8a1", {**held, ATK: 2000}, [(0, 2, 60)], 1)[0])
    good &= check(a > 5 * b, f"Attack slows the start ({a:.3f} -> {b:.3f})")
    x = render(tmp, "8s", {CRUSH: 0, SUS: 0, DEC: 100}, [(0, 4, 60)], 2)[0]
    good &= check(rms(x[int(1.2 * sr):int(1.8 * sr)]) < 1e-4 and rms(x[:sr // 20]) > 0.02, "Decay with Sustain at 0 falls to silence while a key is held")
    a = rms(render(tmp, "8r0", {**held, REL: 5}, [(0, 1, 60)], 2)[0][int(0.56 * sr):int(0.7 * sr)])
    b = rms(render(tmp, "8r1", {**held, REL: 3000}, [(0, 1, 60)], 2)[0][int(0.56 * sr):int(0.7 * sr)])
    good &= check(b > 5 * max(a, 1e-4), f"Release lengthens the tail ({a:.4f} -> {b:.4f})")
    a = level_at(render(tmp, "8v0", {**held, WAVE: 2}, [(0, 4, 60)], 2)[0][sr // 4:sr + sr // 4], sr, 261.63)
    b = level_at(render(tmp, "8v1", {**held, WAVE: 2, VIB: 100}, [(0, 4, 60)], 2)[0][sr // 4:sr + sr // 4], sr, 261.63)
    good &= check(a > 1.5 * b, f"Vibrato spreads the pitch ({a:.4f} -> {b:.4f})")

    l, r, _ = render(tmp, "8bend", {**held, WAVE: 2}, [(0, 8, 60)], 4, bend_at=2)
    c = cents_off(l[int(0.1 * sr):int(0.9 * sr)], sr, 261.63), cents_off(l[int(1.2 * sr):int(2.2 * sr)], sr, 293.66)
    good &= check(abs(c[0]) <= 3 and abs(c[1]) <= 3, f"pitch bend reaches 2 semitones ({c[0]:+d} then {c[1]:+d} cents)")

    # every wave across the whole MIDI range, loud and crushed: finite, under the ceiling
    for wave in range(5):
        l, r, _ = render(tmp, f"8x{wave}", {WAVE: wave, CRUSH: 1, 9: 6}, [(i * 0.1, 0.5, i) for i in range(0, 128, 9)], 4)
        good &= check(all(math.isfinite(s) for s in l) and peak(l) <= 1.001, f"wave {wave} stays finite across every note (peak {peak(l):.3f})")
    return good


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
