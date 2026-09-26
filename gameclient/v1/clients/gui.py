"""Small Tk GUI Client for the shared GameClient Host session."""
from __future__ import annotations

import argparse
import tkinter as tk
from tkinter import ttk

from .base import HostClient, HostClientError


class GameGui:
    def __init__(self, root: tk.Tk, client: HostClient, player_id: str) -> None:
        self.root = root
        self.client = client
        self.player_id = player_id
        self.root.title("GameClient GUI Client")
        self.root.geometry("920x250")
        self.status = tk.StringVar(value="Host: connecting")
        self.info = tk.StringVar(value="")
        self.lease_id: str | None = None
        self.canvas = tk.Canvas(root, height=90, background="white")
        self.canvas.pack(fill="x", padx=20, pady=(20, 8))
        ttk.Label(root, textvariable=self.status).pack()
        ttk.Label(root, textvariable=self.info).pack(pady=(2, 8))
        controls = ttk.Frame(root)
        controls.pack()
        ttk.Button(controls, text="Left", command=lambda: self.move(-1)).grid(row=0, column=0, padx=8)
        ttk.Button(controls, text="Stop", command=lambda: self.move(0)).grid(row=0, column=1, padx=8)
        ttk.Button(controls, text="Right", command=lambda: self.move(1)).grid(row=0, column=2, padx=8)
        ttk.Button(controls, text="Login", command=self.login).grid(row=0, column=3, padx=8)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self.login)

    def login(self) -> None:
        try:
            self.client.login(self.player_id)
            self.status.set(f"Host: ready | player={self.player_id}")
        except HostClientError as exc:
            self.status.set(f"Host error: {exc}")
        self.root.after(50, self.poll)

    def move(self, move_x: int) -> None:
        try:
            if self.player_id == "director1" and self.lease_id is None:
                lease = self.client.control_acquire(transfer=True)["lease"]
                self.lease_id = lease["lease_id"]
            response = self.client.input(move_x, lease_id=self.lease_id)
            self.info.set(
                f"command from GUI: move={move_x} sequence={response.get('sequence')}"
            )
        except HostClientError as exc:
            self.status.set(f"Host error: {exc}")

    def poll(self) -> None:
        try:
            response = self.client.state()
            snapshot = response["snapshot"]
            self.draw(snapshot)
            event = response.get("last_event")
            if event:
                self.info.set(
                    f"last: {event.get('client_id')} {event.get('kind')} "
                    f"move={event.get('move_x', '-')}"
                )
        except HostClientError as exc:
            self.status.set(f"Host error: {exc}")
        finally:
            self.root.after(100, self.poll)

    def draw(self, snapshot: dict) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 800)
        left, right, y = 45, width - 45, 45
        self.canvas.create_line(left, y, right, y, width=2)
        self.canvas.create_text(left, y + 22, text="0")
        self.canvas.create_text(right, y + 22, text="1000")
        length = float(snapshot.get("line_length") or 1000.0)
        for entity in snapshot.get("entities", []):
            x = float(entity.get("x", 0.0))
            sx = left + (right - left) * max(0.0, min(1.0, x / length))
            marker = "P" if entity.get("kind") == "player" else "B" if entity.get("entity_id") == "mob1" else "?"
            self.canvas.create_oval(sx - 12, y - 12, sx + 12, y + 12)
            self.canvas.create_text(sx, y, text=marker)
            self.canvas.create_text(sx, y - 24, text=f"x={x:.1f}")

    def close(self) -> None:
        if self.lease_id is not None:
            try:
                self.client.control_release(self.lease_id)
            except HostClientError:
                pass
            self.lease_id = None
        self.client.close()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameClient GUI Client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17700)
    parser.add_argument("--player", default="player1")
    args = parser.parse_args()
    root = tk.Tk()
    client = HostClient("gui", host=args.host, port=args.port, timeout=1.0)
    GameGui(root, client, args.player)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
