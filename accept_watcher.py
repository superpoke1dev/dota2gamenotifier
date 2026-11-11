"""
Dota 2 Accept-button watcher (Windows).

Watches the screen for the green ACCEPT button and alerts you (sound, Windows
notification, and optionally your phone via ntfy.sh).

Setup:
    pip install mss numpy
Run (Dota must be in Borderless Windowed mode):
    python accept_watcher.py --learn          # first time: learns button position
    python accept_watcher.py                  # afterwards: just watches
Optional phone alerts:
    python accept_watcher.py --ntfy your-private-topic-name
Other options:
    --monitor 2      only use one specific monitor (default: all monitors)
    --reset          forget the learned button position
    --snap           debug: save screenshots when the screen changes a lot
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import winsound

import mss
import mss.tools
import numpy as np

if getattr(sys, "frozen", False):  # running as a packaged .exe
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
TOPIC_FILE = os.path.join(HERE, "ntfy_topic.txt")
CONFIG = os.path.join(HERE, "config.json")
SHOT = os.path.join(HERE, "learned_button.png")
TEMPLATE = os.path.join(HERE, "button_template.npy")
ALERT_SHOT = os.path.join(HERE, "last_alert.png")
POLL = 0.4  # seconds between checks


def green_mask(img):
    """img: BGRA array. True where a pixel looks like the teal-green Accept button."""
    b = img[..., 0].astype(np.int16)
    g = img[..., 1].astype(np.int16)
    r = img[..., 2].astype(np.int16)
    return (g > 55) & (g - r > 20) & (g - b > 5)


def erode(m, k=3):
    """Remove thin lines (like the popup's green border) so only solid blobs remain."""
    out = m.copy()
    for d in range(1, k + 1):
        out[d:, :] &= m[:-d, :]
        out[:-d, :] &= m[d:, :]
    m2 = out.copy()
    for d in range(1, k + 1):
        out[:, d:] &= m2[:, :-d]
        out[:, :-d] &= m2[:, d:]
    return out


def dilate(m, k=6):
    """Grow a mask by k pixels (used to ignore green that was already on screen)."""
    out = m.copy()
    for d in range(1, k + 1):
        out[d:, :] |= m[:-d, :]
        out[:-d, :] |= m[d:, :]
    m2 = out.copy()
    for d in range(1, k + 1):
        out[:, d:] |= m2[:, :-d]
        out[:, :-d] |= m2[:, d:]
    return out


def components(m, max_runs=30000):
    """Find separate solid shapes in a mask. Returns a list of bounding boxes."""
    h, w = m.shape
    pad = np.zeros((h, w + 2), dtype=np.int8)
    pad[:, 1:-1] = m
    d = np.diff(pad, axis=1)
    st, en = np.argwhere(d == 1), np.argwhere(d == -1)
    n = len(st)
    if n == 0 or n > max_runs:
        return []
    rows, s, e = st[:, 0], st[:, 1], en[:, 1]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first = np.searchsorted(rows, np.arange(h + 1))
    for r in range(1, h):
        a0, a1, b0, b1 = first[r - 1], first[r], first[r], first[r + 1]
        i, j = a0, b0
        while i < a1 and j < b1:
            if s[i] < e[j] and s[j] < e[i]:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
            if e[i] < e[j]:
                i += 1
            else:
                j += 1
    comps = {}
    for k in range(n):
        root = find(k)
        sk, rk, ek = int(s[k]), int(rows[k]), int(e[k])
        c = comps.get(root)
        if c is None:
            comps[root] = [sk, rk, ek, rk + 1]
        else:
            c[0], c[1] = min(c[0], sk), min(c[1], rk)
            c[2], c[3] = max(c[2], ek), max(c[3], rk + 1)
    return [{"x0": c[0], "y0": c[1], "x1": c[2], "y1": c[3]} for c in comps.values()]


def on_dark_box(img, x, y, bw, bh):
    """The real popup is a dark box: check the area around the button is mostly dark."""
    h, w = img.shape[:2]
    x0, x1 = max(0, int(x - 0.4 * bw)), min(w, int(x + 1.4 * bw))
    y0, y1 = max(0, int(y - 1.5 * bh)), min(h, int(y + 1.5 * bh))
    area = img[y0:y1, x0:x1, :3]
    return float((area.max(axis=2) < 50).mean()) > 0.6


def save_annotated(shot, img, region_in_mon, path):
    """Save the monitor screenshot with a red box around the learned button."""
    rgb = np.ascontiguousarray(img[..., :3][..., ::-1]).copy()
    h, w = rgb.shape[:2]
    x, y = region_in_mon["x"], region_in_mon["y"]
    bw, bh = region_in_mon["w"], region_in_mon["h"]
    t = 4
    x0, x1 = max(0, x - t), min(w, x + bw + t)
    y0, y1 = max(0, y - t), min(h, y + bh + t)
    for (a, b, c, d) in [(y0, y0 + t, x0, x1), (y1 - t, y1, x0, x1),
                         (y0, y1, x0, x0 + t), (y0, y1, x1 - t, x1)]:
        rgb[a:b, c:d] = (255, 0, 0)
    mss.tools.to_png(rgb.tobytes(), (w, h), output=path)


def find_button(mask, img, cx0, cy0):
    """Look through the green shapes in mask for the one that looks like the Accept button."""
    h, w = img.shape[:2]
    solid = erode(mask)
    if solid.mean() <= 0.0005:
        return None
    # The popup's glow forms a big green frame; the button is a separate solid shape
    # inside it. Look at each shape on its own and pick the button-like one.
    for c in components(solid):
        bw, bh = c["x1"] - c["x0"], c["y1"] - c["y0"]
        if not (w * 0.04 < bw < w * 0.4 and bh > h * 0.015 and 2.5 < bw / max(bh, 1) < 8):
            continue
        # erosion shrank the shape by 3px per side; grow it back
        x0, y0 = max(0, c["x0"] - 3), max(0, c["y0"] - 3)
        x1, y1 = min(mask.shape[1], c["x1"] + 3), min(mask.shape[0], c["y1"] + 3)
        density = float(mask[y0:y1, x0:x1].mean())
        fx, fy = cx0 + x0, cy0 + y0
        # The ACCEPT button is always centered horizontally on the monitor.
        if abs(fx + (x1 - x0) / 2 - w / 2) > 0.03 * w or abs(fy + (y1 - y0) / 2 - h / 2) > 0.20 * h:
            continue
        if density > 0.45 and on_dark_box(img, fx, fy, x1 - x0, y1 - y0):
            return (fx, fy, x1 - x0, y1 - y0, density)
    return None


def to_gray(img):
    """img: BGRA array -> grayscale float array."""
    return 0.299 * img[..., 2] + 0.587 * img[..., 1] + 0.114 * img[..., 0]


def ncc(a, b):
    """Normalized correlation of two same-size arrays (1.0 = identical pattern)."""
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


def alert(msg, ntfy):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
        f"$n.ShowBalloonTip(5000,'Dota 2','{msg}','Info');Start-Sleep 6;$n.Dispose()"
    )
    subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                     creationflags=0x08000000)  # no console window
    if ntfy:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{ntfy}", data=msg.encode(),
                headers={"Title": "Dota 2", "Priority": "urgent", "Tags": "video_game"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print("ntfy failed:", e)
    for _ in range(3):
        winsound.Beep(1200, 250)
        time.sleep(0.1)


def learn(sct, mons):
    """Wait for the Accept popup to NEWLY APPEAR on any monitor, then record where the button is."""
    print(f"Learning mode: watching {len(mons)} monitor(s). Start this BEFORE you queue.")
    state = {}
    for i, mon in mons:
        shot = sct.grab(mon)
        path = os.path.join(HERE, f"monitor_{i}.png")
        mss.tools.to_png(shot.rgb, shot.size, output=path)
        print(f"  monitor {i}: {mon['width']}x{mon['height']} (picture saved as monitor_{i}.png)")
        state[i] = {"bg": None, "streak": 0, "warned": False}
    print("Ignoring any green already on screen. Now queue for a match...")
    last_print = 0.0
    while True:
        stats = []
        for i, mon in mons:
            st = state[i]
            shot = sct.grab(mon)
            img = np.array(shot)
            h, w = img.shape[:2]
            cx0, cx1, cy0, cy1 = int(w * .32), int(w * .68), int(h * .25), int(h * .65)  # around the screen center
            m = green_mask(img[cy0:cy1, cx0:cx1])
            bright = float(img[..., :3].mean())

            if st["bg"] is None:  # first frame = what is already there
                st["bg"] = dilate(m)
                if find_button(m, img, cx0, cy0) and not st["warned"]:
                    st["warned"] = True
                    print(f"  NOTE: monitor {i} already shows something that looks like the Accept "
                          "popup, so I am ignoring it. Decline/accept it, then run this again.")
                continue
            new = m & ~st["bg"]
            stats.append(f"monitor {i}: bright {bright:5.1f} new-green {new.mean() * 100:5.2f}%")
            if bright < 3 and not st["warned"]:
                st["warned"] = True
                print(f"  WARNING: monitor {i} captures as black. Set Dota to Borderless Window.")

            found = find_button(new, img, cx0, cy0)
            st["streak"] = st["streak"] + 1 if found else 0

            if st["streak"] >= 2:  # seen on two checks in a row
                fx, fy, bw, bh, density = found
                time.sleep(0.7)  # let the popup finish fading in, then take a clean picture
                shot = sct.grab(mon)
                img = np.array(shot)
                crop = img[fy:fy + bh, fx:fx + bw]
                if crop.shape[:2] != (bh, bw) or green_mask(crop).mean() < 0.3:
                    st["streak"] = 0
                    continue
                density = float(green_mask(crop).mean())
                np.save(TEMPLATE, to_gray(crop).astype(np.float32))
                region = {"left": mon["left"] + fx, "top": mon["top"] + fy,
                          "width": bw, "height": bh}
                cfg = {"region": region, "baseline": density,
                       "monitor": {k: mon[k] for k in ("left", "top", "width", "height")}}
                json.dump(cfg, open(CONFIG, "w"))
                save_annotated(shot, img, {"x": fx, "y": fy, "w": bw, "h": bh}, SHOT)
                print(f"Found the popup on monitor {i}! Learned button region {region}.")
                print(f"Check {SHOT}: the red box should be around the ACCEPT button.")
                return cfg
        if stats and time.time() - last_print > 1:
            last_print = time.time()
            print("  " + " | ".join(stats))
        time.sleep(POLL)


def snap(sct, mon):
    """Debug helper: save a screenshot whenever the screen changes a lot."""
    folder = os.path.join(HERE, "snaps")
    os.makedirs(folder, exist_ok=True)
    print(f"Snapshot mode on monitor {mon['width']}x{mon['height']}. Queue for a match now.")
    print("When the popup has appeared, press Ctrl+C. Screenshots go in the 'snaps' folder.")
    prev, count, last_save = None, 0, 0.0
    try:
        while True:
            shot = sct.grab(mon)
            small = np.array(shot)[::8, ::8, :3].astype(np.int16)
            if prev is not None:
                d = float(np.abs(small - prev).mean())
                if d > 4 and time.time() - last_save > 3 and count < 8:
                    time.sleep(0.8)  # let the popup finish fading in
                    shot = sct.grab(mon)
                    count += 1
                    last_save = time.time()
                    path = os.path.join(folder, f"change_{count:02d}.png")
                    mss.tools.to_png(shot.rgb, shot.size, output=path)
                    print(f"  big screen change ({d:.1f}) -> saved {path}")
            prev = small
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("Stopped. Send me the change_XX.png files from the snaps folder.")


def watch(sct, cfg, ntfy):
    if "monitor" not in cfg or not os.path.exists(TEMPLATE):
        print("Your saved button is from an older version of this script.")
        print("Run it once with:  python accept_watcher.py --reset --learn")
        return
    region, baseline, mon = cfg["region"], cfg["baseline"], cfg["monitor"]
    template = np.load(TEMPLATE)
    bh, bw = template.shape
    mid_x, mid_y = mon["left"] + mon["width"] / 2, mon["top"] + mon["height"] / 2
    if (abs(region["left"] + bw / 2 - mid_x) > 0.03 * mon["width"]
            or abs(region["top"] + bh / 2 - mid_y) > 0.20 * mon["height"]):
        print("The saved button spot is not at the center of the screen, so it was learned wrongly.")
        print("Run it once with:  python accept_watcher.py --reset --learn")
        return
    threshold = max(0.15, baseline * 0.6)
    MATCH = 0.6  # how closely the spot must look like the learned ACCEPT button
    pad = 3      # tolerate the popup being a few pixels off sideways

    # The button moves up and down depending on the popup's height (for example the extra
    # Dota Plus panel), but it always stays in the same column. Watch a tall strip there.
    up = down = int(mon["height"] * 0.16)  # same reach both ways, so either popup style works
    left = max(mon["left"], region["left"] - pad)
    right = min(mon["left"] + mon["width"], region["left"] + bw + pad)
    top = max(mon["top"], region["top"] - up)
    bottom = min(mon["top"] + mon["height"], region["top"] + bh + down)
    grab_area = {"left": left, "top": top, "width": right - left, "height": bottom - top}
    ox = region["left"] - left  # where the button's column starts inside the strip

    print("Watching for the Accept button. Press Ctrl+C to stop.")
    hits, misses, armed, last_note = 0, 0, True, 0.0
    while True:
        img = np.array(sct.grab(grab_area))
        mask = green_mask(img[:, ox:ox + bw])
        cs = np.concatenate([[0], np.cumsum(mask.sum(axis=1))])
        dens = (cs[bh:] - cs[:-bh]) / float(bw * bh)  # green density for each possible height
        density = float(dens.max()) if len(dens) else 0.0
        score = 0.0
        cand = np.nonzero(dens >= threshold)[0]
        if len(cand):  # cheap check passed, now the careful one
            gray = to_gray(img)
            clusters, start, prev = [], cand[0], cand[0]
            for c in cand[1:]:
                if c - prev > bh // 2:
                    clusters.append((start, prev))
                    start = c
                prev = c
            clusters.append((start, prev))
            for lo, hi in clusters[:4]:
                y_best = lo + int(np.argmax(dens[lo:hi + 1]))
                for y0 in range(y_best - 3, y_best + 4):
                    for dx in range(-pad, pad + 1):
                        x0 = ox + dx
                        if y0 < 0 or x0 < 0 or y0 + bh > gray.shape[0] or x0 + bw > gray.shape[1]:
                            continue
                        score = max(score, ncc(template, gray[y0:y0 + bh, x0:x0 + bw]))
        if len(cand) and score >= MATCH:
            hits, misses = hits + 1, 0
            if hits >= 2 and armed:  # two checks in a row to avoid flickers
                alert("Match found - press ACCEPT!", ntfy)
                print(f"  (green {density:.2f}, button match {score:.2f})")
                try:
                    shot = sct.grab(mon_rect(mon))
                    mss.tools.to_png(shot.rgb, shot.size, output=ALERT_SHOT)
                    print(f"  Screenshot of this moment saved to {ALERT_SHOT}")
                except Exception as e:
                    print("  could not save screenshot:", e)
                armed = False
        else:
            hits = 0
            misses += 1
            if misses >= 3:
                armed = True  # popup gone, re-arm for the next match
            if len(cand) and time.time() - last_note > 10:
                last_note = time.time()
                print(f"  green seen near the button spot but it does not look like ACCEPT "
                      f"(match {score:.2f}) - ignored")
        time.sleep(POLL)


def mon_rect(mon):
    return {"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]}


def get_topic(arg):
    """Phone alert topic: --ntfy option, NTFY_TOPIC variable, or a saved ntfy_topic.txt."""
    if arg:
        return arg
    if os.path.exists(TOPIC_FILE):
        return open(TOPIC_FILE).read().strip()
    if sys.stdin and sys.stdin.isatty():
        t = input("Phone alerts: type your ntfy topic name (or just press Enter to skip): ").strip()
        open(TOPIC_FILE, "w").write(t)  # saved, so you are only asked once
        print(f"Saved. To change it later, edit or delete {TOPIC_FILE}")
        return t
    return ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--learn", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--snap", action="store_true", help="debug: save screenshots on big screen changes")
    p.add_argument("--monitor", type=int, default=0, help="0 = all monitors (default)")
    p.add_argument("--ntfy", default=os.environ.get("NTFY_TOPIC", ""))
    a = p.parse_args()
    a.ntfy = get_topic(a.ntfy)
    if a.ntfy:
        print(f"Phone alerts: ON (topic: {a.ntfy})")
    else:
        print(f"Phone alerts: OFF. To turn them on, put your ntfy topic name in {TOPIC_FILE}")
    print(f"Settings folder: {HERE}")

    if a.reset:
        for f in (CONFIG, TEMPLATE):
            if os.path.exists(f):
                os.remove(f)
        print("Learned button cleared.")

    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
        if a.monitor == 0:
            mons = list(enumerate(sct.monitors))[1:]  # every real monitor
        else:
            mons = [(a.monitor, sct.monitors[a.monitor])]
        if a.snap:
            snap(sct, sct.monitors[0] if a.monitor == 0 else mons[0][1])
            return
        if a.learn or not os.path.exists(CONFIG):
            cfg = learn(sct, mons)
            alert("Match found - press ACCEPT! (this one taught me where the button is)", a.ntfy)
        else:
            cfg = json.load(open(CONFIG))
        try:
            watch(sct, cfg, a.ntfy)
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        if getattr(sys, "frozen", False):
            input("\nPress Enter to close this window...")