"""Sobe a interface local só em 127.0.0.1."""

from __future__ import annotations

import errno
import fcntl
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import uvicorn

from aula_uploader.web.app import WebState, create_app

_CHILD_ENV = "AULA_UPLOADER_WEB_CHILD"
_CLEAN_EXITS = {0, 130, 143, -signal.SIGINT, -signal.SIGTERM}


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    pasta = root / "aula-uploader"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _log_path() -> Path:
    return _cache_dir() / "web.log"


def _lock_path() -> Path:
    return _cache_dir() / "web.lock"


def run_web(*, host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True) -> int:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("A interface web só pode escutar em 127.0.0.1.")
    if os.environ.get(_CHILD_ENV) == "1":
        return _run_child(host=host, port=port)
    return _supervise(host=host, port=port, open_browser=open_browser)


def _port_livre(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            if exc.errno in {errno.EADDRINUSE, getattr(errno, "EADDRNOTAVAIL", -1)}:
                return False
            raise
    return True


def _esperar_porta(host: str, port: int, *, segundos: float = 15.0) -> bool:
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        if _port_livre(host, port):
            return True
        time.sleep(0.25)
    return _port_livre(host, port)


def _acquire_lock() -> object | None:
    """Garante um único supervisor. Retorna o handle aberto ou None se já houver outro."""
    path = _lock_path()
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def _supervise(*, host: str, port: int, open_browser: bool) -> int:
    lock = _acquire_lock()
    if lock is None:
        print()
        print(f"Já existe uma interface em http://{host}:{port}/")
        print("Feche a outra (Ctrl+C no terminal dela) antes de subir de novo.")
        print()
        return 1

    url = f"http://{host}:{port}/"
    log_file = _log_path()
    print()
    print("aula-uploader · interface local")
    print("Só neste computador. Ninguém na rede consegue abrir.")
    print()
    print(url)
    print()
    print("Ctrl+C para encerrar.")
    print()
    if open_browser:
        webbrowser.open(url)

    env = os.environ.copy()
    env[_CHILD_ENV] = "1"
    argv = [sys.executable, "-m", "aula_uploader", "web", "--port", str(port), "--no-browser"]

    child: subprocess.Popen[bytes] | None = None

    def _parar(_signum: int, _frame: object | None) -> None:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _parar)
    signal.signal(signal.SIGTERM, _parar)

    falhas_rapidas = 0
    try:
        while True:
            if not _esperar_porta(host, port, segundos=20.0):
                print(f"Porta {port} ainda ocupada. Liberando e tentando de novo…")
                falhas_rapidas += 1
                time.sleep(min(2**falhas_rapidas, 8))
                continue

            with log_file.open("ab") as log:
                log.write(f"\n--- subindo {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
                log.flush()
                inicio = time.monotonic()
                child = subprocess.Popen(argv, env=env, stdout=log, stderr=log)  # noqa: S603
                codigo = child.wait()
                child = None

            if codigo in _CLEAN_EXITS:
                return 0

            durou = time.monotonic() - inicio
            if durou < 3.0:
                falhas_rapidas += 1
            else:
                falhas_rapidas = 0
            espera = min(2**falhas_rapidas, 8)
            print(f"A interface caiu (código {codigo}). Subindo de novo em {espera}s…")
            time.sleep(espera)
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock.close()


def _run_child(*, host: str, port: int) -> int:
    state = WebState()
    app = create_app(state)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        timeout_keep_alive=75,
    )
    return 0
