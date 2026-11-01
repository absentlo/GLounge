# GLounge

A small band of instruments for [REAPER](https://www.reaper.fm/), made for low-spec PCs. No samples to download: each sound is calculated live, in one small text file.

![GLounge Bass](docs/screenshot.png)

## The band

| Instrument | Status |
|---|---|
| **Bass** | Ready |
| Electric guitar | Planned |
| Drums | Planned |
| Piano | Planned: a soft, felt-style piano rather than a concert grand |

## Install

| System | Download | Then |
|---|---|---|
| **Windows** | [GLounge-Windows.zip](https://github.com/absentlo/GLounge/releases/latest/download/GLounge-Windows.zip) | Unzip it and double-click `install.bat` |
| **Linux** | [GLounge-Linux.tar.gz](https://github.com/absentlo/GLounge/releases/latest/download/GLounge-Linux.tar.gz) | Extract it and run `./install.sh` |

Restart REAPER after installing.

On a portable REAPER, pass its folder to the installer, or copy the `.jsfx` files into its `Effects` folder.

## Play

1. On a track, open the FX window, click **Add** and search for `GLounge`.
2. Arm the track and turn on monitoring.
3. Open **View > Virtual MIDI Keyboard** and play low notes. The bass's open E is E1, MIDI note 28.

## Controls

- **Drag up or down:** turn a knob.
- **Ctrl + drag:** turn it slowly.
- **Right-click:** reset a knob.
- **+ next to the preset list:** save a sound.

## Bass

| Knob | What it does |
|---|---|
| **Pluck** | How sharp the attack is. Velocity adds to it, so playing harder sounds brighter. |
| **Pick** | Fingers at 0, a pick at 1. |
| **Decay** | How long a held note rings. |
| **Tone** | Lower is darker. |
| **Drive** | From clean to a dirty amp. |
| **Level** | Output volume. |

The bass plays one note at a time, like a single string under your hand. Letting go of a key damps the string. Pitch bend reaches 2 semitones up or down.

## Developers

`python3 tests/smoke.py` renders the plugin through REAPER and checks the audio. Pushing a tag like `v0.1.0` builds both downloads and publishes a release.

## License

[MIT](LICENSE)
