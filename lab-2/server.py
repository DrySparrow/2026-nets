import os
import socket
import sys
import threading
import time

from flp import (
    send_packet, recv_packet,
    CMD_UPLOAD_META, CMD_UPLOAD_CHUNK, CMD_UPLOAD_END,
    CMD_OK, CMD_ERROR,
    MAX_FILE_SIZE, MAX_NAME_LEN,
)

UPLOAD_DIR = "uploads"
REPORT_PERIOD = 3.0


class Stats:
    """Потокобезопасный счётчик принятых байт для одного клиента."""
    def __init__(self, peer):
        self.peer = peer
        self.total = 0
        self.start = time.time()
        self.lock = threading.Lock()

    def add(self, n):
        with self.lock:
            self.total += n

    def get_total(self):
        with self.lock:
            return self.total


# форматирование байт/сек
def fmt_speed(bps: float) -> str:
    if bps >= 1024 * 1024:
        return f"{bps / 1024 / 1024:.2f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.2f} KB/s"
    return f"{bps:.2f} B/s"


def reporter(stats: Stats, stop_event: threading.Event, printed: list):
    """Каждые 3 секунды печатает мгновенную и среднюю скорость клиента."""
    last_time = stats.start
    last_bytes = 0
    while True:
        # wait возвращает True, если событие установлено → выходим
        if stop_event.wait(REPORT_PERIOD):
            return
        now = time.time()
        total = stats.get_total()
        dt = now - last_time
        instant = (total - last_bytes) / dt if dt > 0 else 0.0
        elapsed = now - stats.start
        avg = total / elapsed if elapsed > 0 else 0.0
        print(f"[{stats.peer[0]}:{stats.peer[1]}] "
              f"instant={fmt_speed(instant)}, avg={fmt_speed(avg)}, "
              f"total={total} B")
        printed[0] = True
        last_time = now
        last_bytes = total


def unique_path(directory: str, name: str) -> str:
    """Не перезаписываем существующие файлы: file.txt → file_1.txt."""
    path = os.path.join(directory, name)
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(name)
    i = 1
    while True:
        cand = os.path.join(directory, f"{base}_{i}{ext}")
        if not os.path.exists(cand):
            return cand
        i += 1


def handle_client(conn: socket.socket, addr):
    peer = f"{addr[0]}:{addr[1]}"
    print(f"[+] Подключился {peer}")

    stats = Stats(addr)
    stop_event = threading.Event()
    printed = [False]
    rep_thread = threading.Thread(
        target=reporter, args=(stats, stop_event, printed), daemon=True
    )
    rep_thread.start()

    try:
        # --- 1. Метаданные ---
        cmd, payload = recv_packet(conn)
        if cmd != CMD_UPLOAD_META:
            send_packet(conn, CMD_ERROR, b"Expected UPLOAD_META")
            return
        if len(payload) < 8:
            send_packet(conn, CMD_ERROR, b"Bad META payload")
            return

        size = int.from_bytes(payload[:8], "big")
        name_bytes = payload[8:]        # имя файла

        if size > MAX_FILE_SIZE:
            send_packet(conn, CMD_ERROR, b"File too large")
            return
        if len(name_bytes) > MAX_NAME_LEN:
            send_packet(conn, CMD_ERROR, b"Filename too long")
            return

        try:
            name = name_bytes.decode("utf-8")
        except UnicodeDecodeError:
            send_packet(conn, CMD_ERROR, b"Bad filename encoding")
            return

        name = name.replace("\\", "/")
        safe_name = os.path.basename(name)
        if not safe_name:
            send_packet(conn, CMD_ERROR, b"Bad filename")
            return

        out_path = unique_path(UPLOAD_DIR, safe_name)

        # Двойная страховка: убеждаемся, что итоговый путь внутри uploads/
        abs_upload = os.path.abspath(UPLOAD_DIR)
        abs_out = os.path.abspath(out_path)
        if not abs_out.startswith(abs_upload + os.sep):
            send_packet(conn, CMD_ERROR, b"Path escape detected")
            return

        print(f"[{peer}] Принимаю '{safe_name}' ({size} байт)")

        # --- 2. Приём тела файла ---
        received = 0
        with open(out_path, "wb") as f:
            while True:
                cmd, chunk = recv_packet(conn)
                if cmd == CMD_UPLOAD_CHUNK:
                    f.write(chunk)
                    received += len(chunk)
                    stats.add(len(chunk))
                elif cmd == CMD_UPLOAD_END:
                    break
                else:
                    raise ValueError(f"Unexpected cmd 0x{cmd:02X}")
        send_packet(conn, CMD_OK)

    except (ConnectionError, OSError, ValueError) as e:
        print(f"[!] {peer}: {e}")
    finally:
        stop_event.set()
        conn.close()
        print(f"[-] Отключился {peer}")


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <port>")
        sys.exit(1)
    try:
        port = int(sys.argv[1])
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        print("Port must be an integer in 1..65535")
        sys.exit(1)

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:      # IPv4, TCP
        # разрешаем сразу использовать порт после закрытия консоли
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))                              # слушаем на всех сетевых интерфейсах
        srv.listen(64)                        # размер очереди уже установленных, но ещё не принятых соединений
        print(f"[*] Сервер слушает 0.0.0.0:{port}, папка: {UPLOAD_DIR}/")
        try:
            while True:
                conn, addr = srv.accept()
                threading.Thread(
                    target=handle_client, args=(conn, addr), daemon=True
                ).start()
        except KeyboardInterrupt:
            print("\n[*] Остановка сервера...")


if __name__ == "__main__":
    main()