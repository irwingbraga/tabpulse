"""OpenDeck plugin: iTerm2 tab control + activity feedback (incl. Claude Code).

Single process, three event sources feeding one state table:
  - iTerm2 Python API: tabs, selection, focus, screen-update activity
  - Claude Code hooks: exact working / attention / done states (unix socket)
  - OpenDeck (OpenAction websocket): button appear/press, image updates
"""

import asyncio
import base64
import io
import json
import math
import os
import re
import sys
import time
from collections import defaultdict, deque

import iterm2
import websockets
from PIL import Image, ImageDraw, ImageFont

ACTION_UUID = "com.irwingbraga.iterm.tab"
SOCK_PATH = os.path.expanduser("~/.cache/opendeck-iterm/state.sock")
SIZE = 144
SS = 2  # supersampling factor
FPS = 8

COLORS = {
    "idle": (130, 130, 138),  # gray
    "running": (255, 204, 0),  # yellow (output activity in a regular tab)
    "working": (255, 204, 0),  # yellow (Claude working), same as iTerm's Claude indicator
    "attention": (255, 95, 70),  # red-orange, distinct from yellow
    "done": (52, 199, 89),  # green
}
ANIMATED = {"running", "working", "attention"}
# Claude "working" with no screen output for this long is assumed interrupted.
WORKING_STALE_S = 15
RUNNING_WINDOW_S = 1.5
RUNNING_MIN_UPDATES = 3

FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONT_PATH, size * SS, index=1 if bold else 0)
    except OSError:
        return ImageFont.load_default()


def clean_title(title):
    """Drop the trailing "(job)" iTerm appends, e.g. "Chat (claude)" -> "Chat"."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", title or "").strip() or (title or "")


def wrap(d, text, f, max_w, max_lines):
    """Greedy wrap: break at spaces, then after - or /, then mid-word; ellipsis if cut."""
    units = []  # (text, joined_to_previous)
    for word in text.split():
        parts = re.findall(r"[^-/]*[-/]|[^-/]+", word)
        for i, part in enumerate(parts):
            units.append((part, i > 0))
    lines, cur = [], ""
    for part, joined in units:
        while d.textlength(part, font=f) > max_w:  # single unit too wide: split it
            n = len(part)
            while n > 1 and d.textlength(part[:n], font=f) > max_w:
                n -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(part[:n])
            part, joined = part[n:], True
        trial = cur + part if (joined or not cur) else cur + " " + part
        if d.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = part
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and d.textlength(last + "…", font=f) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


# ---------------------------------------------------------------- state ----


class State:
    def __init__(self):
        self.buttons = {}  # context -> {"device","row","col","settings"}
        self.claude = {}  # session_id -> (state, ts)
        self.updates = defaultdict(lambda: deque(maxlen=16))  # session_id -> ts
        self.iterm_active = True
        self.tabs = []  # list of dicts: {"sid","title","selected"}
        self.last_sent = {}  # context -> frame key
        self.app = None
        self.all_tabs = []  # every tab in every window

    def ordered_contexts(self):
        return sorted(
            self.buttons,
            key=lambda c: (
                self.buttons[c]["device"],
                self.buttons[c]["row"],
                self.buttons[c]["col"],
            ),
        )

    def tab_for(self, ctx):
        sid = self.buttons[ctx]["settings"].get("sid")
        if sid:  # pinned to a specific tab; blank if that tab was closed
            return next((t for t in self.all_tabs if t["sid"] == sid), None)
        auto = [c for c in self.ordered_contexts() if not self.buttons[c]["settings"].get("sid")]
        idx = auto.index(ctx)
        return self.tabs[idx] if idx < len(self.tabs) else None

    def effective(self, tab, now):
        sid = tab["sid"]
        last = self.updates[sid][-1] if self.updates[sid] else 0
        c = self.claude.get(sid)
        if c:
            cstate, ts = c
            if cstate == "working" and now - max(last, ts) > WORKING_STALE_S:
                self.claude.pop(sid, None)
            elif cstate == "done" and tab["selected"] and self.iterm_active:
                self.claude.pop(sid, None)  # acknowledged by looking at it
            elif cstate in ("working", "attention", "done"):
                return cstate
        if not (tab["selected"] and self.iterm_active):
            recent = [t for t in self.updates[sid] if now - t < RUNNING_WINDOW_S]
            if len(recent) >= RUNNING_MIN_UPDATES:
                return "running"
        return "idle"


S = State()

# ------------------------------------------------------------- rendering ---


# Foreground (glyph + label) per state: dark on bright backgrounds, white otherwise.
FG = {
    "idle": (240, 240, 244),
    "running": (30, 26, 0),
    "working": (30, 26, 0),
    "attention": (255, 255, 255),
    "done": (255, 255, 255),
}
BG_IDLE = (84, 84, 92)


def render(title, state, phase, selected, index):
    bg = BG_IDLE if state == "idle" else COLORS[state]
    fg = FG[state]
    track = tuple(int(c * 0.78) for c in bg)
    img = Image.new("RGB", (SIZE * SS, SIZE * SS), bg)
    d = ImageDraw.Draw(img)
    cx, cy, r = SIZE * SS // 2, 33 * SS, 22 * SS
    box = [cx - r, cy - r, cx + r, cy + r]
    w = 6 * SS
    u = SS  # one design pixel

    if state in ("running", "working"):
        d.arc(box, 0, 360, fill=track, width=w)
        start = (phase * 360 / FPS / (1.0 if state == "working" else 1.6)) % 360
        d.arc(box, start, start + 110, fill=fg, width=w)
    elif state == "attention":
        pulse = 0.5 + 0.5 * math.sin(phase * 2 * math.pi / FPS)
        c = tuple(int(a + (b - a) * pulse) for a, b in zip(track, fg))
        d.ellipse(box, outline=c, width=w)
        d.rectangle([cx - 3 * u, cy - 13 * u, cx + 3 * u, cy + 3 * u], fill=c)
        d.ellipse([cx - 3.5 * u, cy + 8 * u, cx + 3.5 * u, cy + 15 * u], fill=c)
    elif state == "done":
        d.ellipse(box, outline=fg, width=w)
        d.line(
            [(cx - 10 * u, cy), (cx - 3 * u, cy + 8 * u), (cx + 11 * u, cy - 8 * u)],
            fill=fg,
            width=5 * u,
            joint="curve",
        )
    else:
        d.ellipse(box, outline=fg, width=w)
        d.text((cx, cy), str(index + 1), font=font(24, bold=True), fill=fg, anchor="mm")

    f = font(23, bold=True)
    lines = wrap(d, clean_title(title) or "(no tab)", f, (SIZE - 14) * SS, 3)
    line_h = 25 * SS
    top = 66 * SS + ((3 - len(lines)) * line_h) // 2  # center the block in the label area
    for i, line in enumerate(lines):
        d.text((cx, top + i * line_h + line_h // 2), line, font=f, fill=fg, anchor="mm")

    if selected:
        d.rectangle([0, 0, SIZE * SS - 1, SIZE * SS - 1], outline=(255, 255, 255), width=5 * SS)

    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# --------------------------------------------------------------- iTerm2 ----


async def iterm_task():
    while True:
        try:
            conn = await iterm2.Connection.async_create()
            app = await iterm2.async_get_app(conn)
            S.app = app
            await asyncio.gather(
                sync_tabs(app), watch_sessions(app, conn), watch_focus(conn), return_exceptions=False
            )
        except Exception as e:  # iTerm not running / API disabled / dropped
            print(f"iterm: {e!r}, retrying", file=sys.stderr, flush=True)
            S.tabs, S.all_tabs, S.app = [], [], None
            await asyncio.sleep(3)


async def sync_tabs(app):
    while True:
        win = app.current_terminal_window
        tabs = []
        all_tabs = []
        for wi, w in enumerate(app.terminal_windows):
            for ti, tab in enumerate(w.tabs):
                sess = tab.current_session
                if not sess:
                    continue
                try:
                    name = await tab.async_get_variable("title")
                except Exception:
                    name = ""
                all_tabs.append(
                    {
                        "sid": sess.session_id,
                        "title": (name or "").strip(),
                        "selected": w is win and tab is w.current_tab,
                        "win": wi + 1,
                        "pos": ti + 1,
                    }
                )
        S.all_tabs = all_tabs
        if win:
            for tab in win.tabs:
                sess = tab.current_session
                if not sess:
                    continue
                try:
                    name = await tab.async_get_variable("title")
                except Exception:
                    name = ""
                tabs.append(
                    {
                        "sid": sess.session_id,
                        "title": (name or "").strip(),
                        "selected": tab is win.current_tab,
                    }
                )
        S.tabs = tabs
        await asyncio.sleep(0.5)


async def watch_sessions(app, conn):
    """One screen streamer per session; timestamps feed the activity heuristic."""
    tasks = {}

    async def stream(sess):
        try:
            async with sess.get_screen_streamer(want_contents=False) as st:
                while True:
                    await st.async_get(style=False)
                    S.updates[sess.session_id].append(time.monotonic())
        except Exception:
            pass

    try:
        while True:
            live = {s.session_id: s for w in app.terminal_windows for t in w.tabs for s in t.sessions}
            for sid, sess in live.items():
                if sid not in tasks or tasks[sid].done():
                    tasks[sid] = asyncio.create_task(stream(sess))
            for sid in [s for s in tasks if s not in live]:
                tasks.pop(sid).cancel()
                S.updates.pop(sid, None)
                S.claude.pop(sid, None)
            await asyncio.sleep(1)
    finally:
        for t in tasks.values():
            t.cancel()


async def watch_focus(conn):
    async with iterm2.FocusMonitor(conn) as mon:
        while True:
            u = await mon.async_get_next_update()
            if u.application_active is not None:
                S.iterm_active = u.application_active


async def activate_tab(ctx):
    tab = S.tab_for(ctx)
    if not tab:
        return
    try:
        sess = S.app.get_session_by_id(tab["sid"])
        if sess:
            await sess.async_activate(select_tab=True, order_window_front=True)
            await S.app.async_activate()
    except Exception as e:
        print(f"activate failed: {e!r}", file=sys.stderr, flush=True)


# ---------------------------------------------------------- Claude hooks ---


async def claude_server():
    os.makedirs(os.path.dirname(SOCK_PATH), exist_ok=True)
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)

    async def handle(reader, writer):
        try:
            async for line in reader:
                msg = json.loads(line)
                if msg["state"] == "idle":
                    S.claude.pop(msg["session"], None)
                else:
                    S.claude[msg["session"]] = (msg["state"], time.monotonic())
        except Exception as e:
            print(f"hook msg error: {e!r}", file=sys.stderr, flush=True)
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, SOCK_PATH)
    async with server:
        await server.serve_forever()


# -------------------------------------------------------------- OpenDeck ---


async def opendeck_task(port, uuid, register_event):
    async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
        await ws.send(json.dumps({"event": register_event, "uuid": uuid}))

        async def renderer():
            while True:
                now = time.monotonic()
                phase = int(time.time() * FPS) % FPS
                for ctx in list(S.buttons):
                    tab = S.tab_for(ctx)
                    if tab:
                        state = S.effective(tab, now)
                        key = (tab["title"], state, tab["selected"] and S.iterm_active,
                               phase if state in ANIMATED else 0)
                    else:
                        state, key = "idle", ("none",)
                    if S.last_sent.get(ctx) == key:
                        continue
                    S.last_sent[ctx] = key
                    idx = S.ordered_contexts().index(ctx)
                    img = render(tab["title"] if tab else "", state, phase,
                                 bool(tab and tab["selected"] and S.iterm_active), idx)
                    await ws.send(json.dumps(
                        {"event": "setImage", "context": ctx, "payload": {"image": img, "target": 0}}))
                await asyncio.sleep(1 / FPS)

        render_task = asyncio.create_task(renderer())
        try:
            async for raw in ws:
                ev = json.loads(raw)
                name, ctx = ev.get("event"), ev.get("context")
                if ev.get("action") != ACTION_UUID:
                    continue
                if name == "willAppear":
                    p = ev.get("payload", {})
                    co = p.get("coordinates", {})
                    S.buttons[ctx] = {
                        "device": ev.get("device", ""),
                        "row": co.get("row", 0),
                        "col": co.get("column", 0),
                        "settings": p.get("settings") or {},
                    }
                    S.last_sent.pop(ctx, None)
                elif name == "willDisappear":
                    S.buttons.pop(ctx, None)
                    S.last_sent.clear()
                elif name == "didReceiveSettings" and ctx in S.buttons:
                    S.buttons[ctx]["settings"] = ev.get("payload", {}).get("settings") or {}
                    S.last_sent.clear()
                elif name == "sendToPlugin":
                    await ws.send(json.dumps({
                        "event": "sendToPropertyInspector", "action": ACTION_UUID, "context": ctx,
                        "payload": {"tabs": [
                            {"sid": t["sid"], "label": f'W{t["win"]} T{t["pos"]}: {t["title"] or "(untitled)"}'}
                            for t in S.all_tabs]}}))
                elif name == "keyDown" and ctx in S.buttons:
                    asyncio.create_task(activate_tab(ctx))
        finally:
            render_task.cancel()


def parse_args(argv):
    a = {argv[i].lstrip("-"): argv[i + 1] for i in range(0, len(argv) - 1, 2)}
    return int(a["port"]), a["pluginUUID"], a["registerEvent"]


async def main():
    port, uuid, reg = parse_args(sys.argv[1:])
    await asyncio.gather(opendeck_task(port, uuid, reg), iterm_task(), claude_server())


if __name__ == "__main__":
    asyncio.run(main())
