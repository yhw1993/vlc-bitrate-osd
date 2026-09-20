# VLC Bitrate OSD

Real-time bitrate, frame-drop and stream-drift monitoring for VLC — overlaid on the player itself.

![One VLC window with the OSD overlay pinned to its top-right corner](assets/screenshot-osd.png)

yhw1993

VLC gives you the picture but no live view of stream health. This project adds two independent ways to see the numbers:

- **Python OSD overlay** — a translucent always-on-top window showing instantaneous / average / peak bitrate, frame drops, audio buffer statistics and a live bitrate curve. Talks to VLC over its built-in HTTP interface.
- **VLC Lua extension** — an in-player overlay built on VLC's own OSD channel and marquee filter. No extra process.

It also ships **`vlc_sync.py`**, a timestamp synchronizer that keeps two VLC instances aligned — typically a device stream arriving over RTSP against the original local file, so a re-encoded stream can be compared against its source frame by frame.

## Why

If you tune encoders, chase bitrate-control bugs, or verify what a camera actually sends, you keep asking:

- Is the stream really running at the configured bitrate, or is it bursting?
- Where does it peak?
- Are frames being dropped, and how fast?
- Does the re-encoded stream stay in sync with the source?

VLC is the obvious player to use and it already collects all of this internally — it just never shows it. These tools surface it.

## Requirements

- VLC 3.0+
- Python 3.7+ **with tkinter**
- **No third-party packages** — standard library only (`urllib`, `xml.etree`, `tkinter`, `ctypes`)

Overlay window-pinning is Windows-specific (`EnumWindows`). Everything else is portable.

## Quick start

### 1. Start VLC with the HTTP interface

```
vlc --extraintf http --http-port 8080 --http-password vlc123
```

On Windows you can instead run `scripts/start_vlc_osd.bat`. Machine-specific paths (your Python interpreter, your VLC install) belong in `scripts/local.bat` — it is git-ignored, so they never reach the repository. The launchers also verify that the Python they are about to use actually has tkinter, rather than starting an overlay that dies on import.

### 2. Start the overlay

```
python vlc_bitrate_monitor.py --password vlc123
```

Play something. The overlay attaches to the top-right of the VLC video window and starts reporting.

### 3. Hotkeys

| Input       | Action                   |
| ----------- | ------------------------ |
| drag        | move the window          |
| `G`         | toggle the bitrate graph |
| `+` / `-`   | opacity                  |
| `Esc` / `Q` | quit                     |

## Two instances side by side

For A/B comparisons — two encodes, or a stream against its source:

```
scripts/start_two_vlc.bat
```

Launches two VLC instances (HTTP 8080 / 8081), each with its own overlay, pinned to the matching VLC window through `--instance 0` / `--instance 1`.

![Two overlays, one per VLC instance](assets/screenshot-dual.png)

## Keeping two players in sync

`vlc_sync.py` treats one player as the master clock (by default the RTSP stream, which you cannot control) and continuously nudges the other towards it.

```
python vlc_sync.py --a 127.0.0.1:8080 --b 127.0.0.1:8081 --password vlc123 --offset 3
```

| Flag          | Meaning                                                                               | Default                    |
| ------------- | ------------------------------------------------------------------------------------- | -------------------------- |
| `--a` / `--b` | `host:port` of VLC A / B                                                              | `127.0.0.1:8080` / `:8081` |
| `--master`    | master clock side (`a` or `b`)                                                        | `b`                        |
| `--offset`    | wait until the master has played N seconds before aligning — covers network buffering | `3`                        |
| `--drift`     | tolerated drift, seconds                                                              | `0.5`                      |
| `--interval`  | correction polling interval, seconds                                                  | `0.5`                      |
| `--once`      | align once, then exit                                                                 | off                        |

**How it holds sync**

1. Wait for the master to pass `--offset` seconds, so the RTSP buffering period is behind it.
2. Pause the slave, seek it to the master's position, resume both.
3. Poll. Fallen behind → seek forward. Run ahead → pause just long enough for the master to catch up (capped at 3 s per correction).

## How it works

### Bitrate is computed from byte deltas, not from VLC's own figure

VLC exposes `demuxbitrate` and `inputbitrate`, but both are **exponential moving averages** — they lag, and they do not fall to zero when playback pauses. This tool samples `demuxreadbytes` and divides the delta by elapsed time, giving a true instantaneous rate.

Two wrinkles worth knowing before extending the code:

- **Units.** VLC's internal bitrate values arrive in MB/s, not bits/s. Values below 1000 are multiplied by 1048576 in `BitrateTracker.update()` to normalise everything to bytes/s.
- **Local files.** Once VLC has read a local file into its cache the byte counter stops advancing, so the delta goes to zero while playback continues. The code then falls back to VLC's internal smoothed value — and that fallback only fires while `state == "playing"`, otherwise a paused player would report a stale non-zero bitrate.

### Window pinning

The overlay locates VLC windows via `EnumWindows` (through `ctypes`), sorts them by area, and positions itself against the N-th one — that is what `--instance` selects. If no VLC window is found it falls back to a screen-corner offset so multiple overlays do not stack.

### Why hotkeys are bound globally

The window is created with `overrideredirect(True)` to remove the title bar. On Windows such windows never take keyboard focus, so a normal `bind()` on the root window never fires. The code uses `bind_all()` and calls `unbind_all()` on exit so the bindings do not leak into other applications.

### VLC 3.0 seek bug

`vlc_sync.py` only ever seeks to whole seconds. VLC 3.0's HTTP `seek` command mishandles fractional values — `seek&val=13.52` jumps to 52 s — so the code rounds to integers and lets the drift-correction loop absorb the residual error.

### Pause command

`in_pause` pauses; `pl_pause` toggles. The sync script prefers `in_pause` and falls back to `pl_pause` on builds that do not implement it.

## Lua extension (in-player alternative)

`lua/bitrate_osd.lua` uses VLC's own OSD channel plus the marquee filter, so nothing runs outside the player.

Copy it into VLC's extensions directory and restart VLC:

| Platform | Path                                                             |
| -------- | ---------------------------------------------------------------- |
| Windows  | `%APPDATA%\vlc\lua\extensions\`                                  |
| Linux    | `~/.local/share/vlc/lua/extensions/`                             |
| macOS    | `~/Library/Application Support/org.videolan.vlc/lua/extensions/` |

Then open **View → Bitrate OSD**.

|                   | Lua extension                  | Python overlay                   |
| ----------------- | ------------------------------ | -------------------------------- |
| Runs inside VLC   | yes                            | no                               |
| Overlay mechanism | VLC OSD + marquee              | translucent always-on-top window |
| Bitrate graph     | text bar                       | real-time canvas curve           |
| Stability         | depends on the Lua API surface | no internal API use              |
| Multi-instance    | one per player                 | `--instance` pins each overlay   |
| Best for          | a quick look                   | long monitoring sessions         |

## CLI reference

### vlc_bitrate_monitor.py

| Flag         | Default     | Meaning                       |
| ------------ | ----------- | ----------------------------- |
| `--host`     | `127.0.0.1` | VLC HTTP host                 |
| `--port`     | `8080`      | VLC HTTP port                 |
| `--password` | (empty)     | VLC HTTP password             |
| `--interval` | `500`       | refresh interval, ms          |
| `--no-graph` | off         | hide the bitrate curve        |
| `--instance` | `0`         | which VLC window to attach to |

### vlc_sync.py

See the flag table above.

`scripts/make_test_video.py` generates a clip with a burned-in frame counter and timecode — the practical way to verify that sync is actually holding, since you can read the frame number off both windows.

## Known limitations

- **Window pinning is Windows-only.** It uses a Win32 call. The overlay still runs elsewhere but you position it yourself.
- **Live streams cannot be seeked.** An RTSP live stream has no seekable length, so alignment is limited to pause/resume and precision is bounded by the GOP length.
- **`--drift` defaults to 0.5 s** because a seek lands on the nearest keyframe rather than an exact timestamp. Tightening it below the GOP duration causes constant corrections.
- **This reads statistics, it does not decode.** Every number comes from VLC's own counters; a field VLC does not report cannot be recovered here.

## Troubleshooting

| Symptom                         | Cause / fix                                                                                        |
| ------------------------------- | -------------------------------------------------------------------------------------------------- |
| Overlay says "not connected"    | VLC is running without the HTTP interface. Restart it with `--extraintf http`.                     |
| Bitrate reads 0 while playing   | The byte counter is not advancing (local file fully cached) *and* VLC reports no internal bitrate. |
| Overlay sits in a screen corner | No VLC window matched — the title must contain both "VLC" and "media player".                      |
| Hotkeys do nothing              | Another app holds the global bindings. Click the overlay once, then press again.                   |
| Sync keeps correcting           | `--drift` is below the GOP duration. Raise it.                                                     |
| HTTP 401                        | Password mismatch between VLC's `--http-password` and the tool's `--password`.                     |
| Launcher: no Python with tkinter | Your default `python` lacks tkinter — common with bundled/embedded interpreters. Point `PYTHON` in `scripts/local.bat` at a full interpreter that has it. |

## License

MIT — see [LICENSE](LICENSE).
