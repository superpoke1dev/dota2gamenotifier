# Dota 2 Accept Watcher

Alerts you when the Dota 2 **"Your game is ready – ACCEPT"** popup appears, so you can alt-tab away
while queueing and still not miss it. You get a sound and a Windows notification on your PC, and
optionally a push notification on your phone.

It works by watching your screen for the ACCEPT button. It never clicks anything for you.

> **Status:** developed and tested on Windows 11, 1920x1080. Other setups may need tweaks (see
> [Limitations](#limitations)). Windows only.

## Quick start

### Option A: the app (no Python needed)
1. Download `DotaAcceptWatcher.exe` from the [Releases](../../releases) page.
2. Put it in its **own folder** (for example `Desktop\DotaWatcher`) and always run it from there.
   It saves its settings next to itself.
3. Double-click it. See [First run](#first-run).

Windows may show a "Windows protected your PC" (SmartScreen) or antivirus warning because the app is
not code-signed. If you'd rather not trust a download, build it yourself (Option B/C below).

### Option B: run the script
1. Install [Python 3.10+](https://www.python.org/downloads/) (tick **Add Python to PATH**).
2. In a terminal in this folder:
   ```
   python -m pip install -r requirements.txt
   python accept_watcher.py
   ```

### Option C: build your own .exe
Double-click `build_exe.bat`. The app appears in `dist\DotaAcceptWatcher.exe`. Copy it to its own
folder before using it.

## First run

1. In Dota 2, set the display mode to **Borderless Window**. In true fullscreen, Windows can't
   capture the game. If the setting keeps resetting, use the Steam launch option
   `-windowed -noborder` (right-click Dota 2 → Properties → Launch Options).
2. Start the app **before** you queue. It will ask for a phone topic (see below). Press Enter to skip.
3. It starts in **learn mode**: it watches every monitor and waits for the first ACCEPT popup to
   appear. Queue for a match. When the popup shows, you'll hear beeps and see
   `Found the popup on monitor X!`.
4. Open `learned_button.png` (next to the app). A red box should be around the ACCEPT button.
5. From now on, just start the app whenever you queue. It's silent until the popup appears.

To start over (different monitor, resolution or UI scale), run with `--reset --learn`
(`python accept_watcher.py --reset --learn`) or delete `config.json` and `button_template.npy`.

## Phone alerts (optional)

Uses the free [ntfy](https://ntfy.sh) service.

1. Install the **ntfy** app on your phone.
2. Subscribe to a topic with a **long, random name**, e.g. `dota-accept-k29x7f41`. Anyone who knows
   the topic name can read your alerts, so don't use something guessable.
3. Give the same name to the watcher, either by typing it at the first-run prompt, by putting it in
   `ntfy_topic.txt` next to the app, or with `python accept_watcher.py --ntfy your-topic`.
4. On startup the app prints `Phone alerts: ON (topic: ...)` or `Phone alerts: OFF`.

Tip: in the ntfy app, allow it to override Do Not Disturb so you don't miss the alert.

## Options

| Option | What it does |
|---|---|
| `--learn` | Re-learn where the ACCEPT button is |
| `--reset` | Forget the learned button |
| `--ntfy TOPIC` | Phone alert topic (or set the `NTFY_TOPIC` environment variable) |
| `--monitor N` | Only watch monitor N (default: all monitors) |
| `--snap` | Debug: save screenshots when the screen changes a lot |

## How it works

- Learn mode looks for a button-shaped teal-green area that **newly appears** in the middle of a
  monitor, sitting on the dark popup box. It saves the button's position and a small grayscale
  picture of it (`button_template.npy`).
- Watch mode checks a tall strip in the button's column (so the Dota Plus popup, where the button
  sits higher, also works). It alerts only if the area is green **and** matches the saved picture of
  the ACCEPT button, which avoids false alarms from other green buttons in the menu.
- Everything happens on your computer. The only network traffic is the short text
  "Match found - press ACCEPT!" sent to ntfy.sh if you turn phone alerts on. No screenshots leave
  your PC.

## Troubleshooting

| Problem | Try |
|---|---|
| Never learns / never alerts | Open `monitor_1.png` / `monitor_2.png` (saved at startup). If Dota isn't visible in either, switch to Borderless Window. |
| Alerts on the menu | Send/inspect `last_alert.png`, then `--reset --learn`. |
| "It does not look like ACCEPT" messages | The green area is being ignored on purpose. If they appear during a real popup, re-learn. |
| No phone alerts | Check the startup line says `Phone alerts: ON` and the topic matches the app exactly. |
| `.exe` build says "Access is denied" | Close any running `DotaAcceptWatcher.exe`, wait a few seconds, run `build_exe.bat` again. |
| `No module named 'mss'` | Run `python -m pip install -r requirements.txt`. |

## Limitations

- Windows only (uses Windows notifications and sounds).
- Tested at 1920x1080. It assumes the ACCEPT button is centered horizontally on the monitor and
  within about 20% of the vertical middle. Other resolutions should work through learn mode but
  are untested.
- Popup layouts that move the button more than roughly 16% of the screen height from where it was
  learned would be missed.
- It reads screen pixels, so Dota must be visible: not minimized, and in borderless/windowed mode.

## Disclaimer

Not affiliated with or endorsed by Valve. Dota 2 is a trademark of Valve Corporation. This tool only
takes screenshots of your own screen (like any screen-capture program). It doesn't touch the game's
files or memory and doesn't send input to the game. Use at your own risk.

## License

MIT, see [LICENSE](LICENSE).
