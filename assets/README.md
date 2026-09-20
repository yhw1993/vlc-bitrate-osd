# Screenshots

The two images the READMEs refer to are **not committed yet**, so both
`README.md` and `README.zh-CN.md` currently carry their image embeds commented
out — that way a fresh clone renders cleanly instead of showing broken images.

| File | What it should show |
|---|---|
| `screenshot-osd.png` | One VLC window with the OSD overlay pinned to its top-right corner |
| `screenshot-dual.png` | Two VLC windows, each with its own overlay |

## Capture them safely

These images go public. A full-screen capture leaks your desktop, taskbar, other
application windows, file paths, and whatever video happens to be playing.

- Capture **only the VLC window plus the overlay**: `Alt+PrintScreen` for the
  active window, or `Win+Shift+S` and drag a tight rectangle.
- Use a **test clip, not personal media**. Generate one first:

  ```
  python scripts/make_test_video.py -o out/test_video.avi --check
  ```

  It writes a raw-DIB AVI with a burned-in frame number and timecode, so the
  sync feature can be verified by eye as well.
- Before committing, open the image and check for: desktop icons, taskbar, other
  windows, folder paths, chat windows.
- Crop the VLC title bar if the file name shown in it is sensitive.

## Wiring them back up

Once the two PNGs are in place, remove the comment markers:

1. In `README.md` — uncomment the embed after the tagline and the one under
   "Two instances side by side".
2. In `README.zh-CN.md` — uncomment the embed near the top and the one under
   "两个实例并排".

Then delete this file.
