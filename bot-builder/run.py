"""Запуск конструктора ботов: python run.py"""
import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402


def local_ip() -> str:
    """IP этого ПК в домашней сети — чтобы открыть интерфейс с телефона."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Конструктор ботов")
    ap.add_argument("--host", default="0.0.0.0",
                    help="0.0.0.0 — доступ с телефона по Wi-Fi, 127.0.0.1 — только этот ПК")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true",
                    help="не открывать браузер автоматически")
    args = ap.parse_args()

    print()
    print("=" * 58)
    print("  🤖 Конструктор ботов")
    print(f"  На этом ПК:   http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        print(f"  С телефона:   http://{local_ip()}:{args.port}   (тот же Wi-Fi)")
    print("  Остановить сервер: Ctrl+C")
    print("=" * 58)
    print()

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    from app.main import app
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
