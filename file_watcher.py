"""File watcher utility using watchdog.

When imported and `start_watcher` is called, it will watch a given directory for
create / modify / move / delete events and push notifications via the existing
`NotificationManager` (Telegram + Email), emit a Socket.IO event if a `socketio`
instance is provided, and also show desktop toast notifications (Windows or cross-platform).

Usage::

    from file_watcher import start_watcher
    from notifier import NotificationManager
    from app import socketio  # optional

    nm = NotificationManager()
    start_watcher(path="./watched_dir", notifier=nm, socketio=socketio)

If you do not pass a `socketio` instance, the watcher will still send Telegram /
Email alerts and desktop notifications.
"""

from __future__ import annotations

import threading
import time
import warnings
from pathlib import Path
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

try:
    from flask_socketio import SocketIO  # optional
except ModuleNotFoundError:
    SocketIO = None  # type: ignore

from notifier import NotificationManager

# === Toast Notification Support ===

# Try win10toast (Windows only) and suppress deprecation warning
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=UserWarning, module="win10toast")
    try:
        from win10toast import ToastNotifier
        _toast = ToastNotifier()
        _toast_type = "win10toast"
    except ImportError:
        _toast = None
        _toast_type = None

# Try plyer (cross-platform fallback)
try:
    from plyer import notification as plyer_notification
    _has_plyer = True
except ImportError:
    _has_plyer = False


def _human_readable(event: FileSystemEvent) -> str:
    """Return a concise human-readable message for the event."""
    event_map = {
        "created": "Created",
        "modified": "Modified",
        "deleted": "Deleted",
        "moved": "Moved"
    }
    action = event_map.get(event.event_type, event.event_type.capitalize())
    return f"{action}: {event.src_path}"


class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, notifier: NotificationManager, socketio: Optional["SocketIO"], path_label: str):
        super().__init__()
        self.notifier = notifier
        self.socketio = socketio
        self.path_label = path_label

    def on_any_event(self, event: FileSystemEvent):  # noqa: N802
        if event.is_directory:
            return

        message = _human_readable(event)
        print(f"[WATCHDOG] {message}")

        # 1. Emit real-time browser notification
        if self.socketio:
            try:
                self.socketio.emit("file_change", {"message": message, "path": event.src_path})
            except Exception as e:
                print(f"[SocketIO ERROR] {e}")

        # 2. Telegram + Email
        try:
            self.notifier.send_telegram(f"🗂️ {self.path_label}: {message}")
            self.notifier.send_email(
                subject=f"File change detected – {self.path_label}",
                body=message,
                to_email=self.notifier.email_user
            )
        except Exception as e:
            print(f"[Notifier ERROR] {e}")

        # 3. Toast Notification (Desktop)
        try:
            if _toast_type == "win10toast" and _toast:
                _toast.show_toast(
                    "📂 File Watcher Alert",
                    message,
                    duration=5,
                    threaded=True
                )
            elif _has_plyer:
                plyer_notification.notify(
                    title="📂 File Watcher Alert",
                    message=message,
                    timeout=5
                )
        except Exception as e:
            print(f"[TOAST ERROR] {e}")


def start_watcher(
    path: str | Path,
    *,
    notifier: Optional[NotificationManager] = None,
    socketio: Optional["SocketIO"] = None,
    recursive: bool = True
) -> None:
    """Start watchdog observer in a daemon thread.

    Parameters
    ----------
    path : str | Path
        The directory to monitor for changes.
    notifier : NotificationManager, optional
        Instance for sending Telegram/Email alerts.
    socketio : flask_socketio.SocketIO, optional
        Instance to emit live browser events.
    recursive : bool
        Whether to monitor subfolders recursively.
    """
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Watch path does not exist: {path}")

    notifier = notifier or NotificationManager()
    handler = _ChangeHandler(notifier=notifier, socketio=socketio, path_label=str(path))

    observer = Observer()
    observer.schedule(handler, str(path), recursive=recursive)

    def _run():
        observer.start()
        try:
            while observer.is_alive():
                time.sleep(1)
        finally:
            observer.stop()
            observer.join()

    thread = threading.Thread(target=_run, name="file-watcher", daemon=True)
    thread.start()
    print(f"[WATCHDOG] Started monitoring: {path}")
