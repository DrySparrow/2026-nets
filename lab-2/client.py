import os
import socket
import sys

from flp import (
    send_packet, recv_packet,
    CMD_UPLOAD_META, CMD_UPLOAD_CHUNK, CMD_UPLOAD_END,
    CMD_OK, CMD_ERROR,
    CHUNK_SIZE, MAX_FILE_SIZE, MAX_NAME_LEN,
)


def main():
    if len(sys.argv) != 4:
        print(f"Usage: python {sys.argv[0]} <file> <host> <port>")
        sys.exit(1)

    filepath = sys.argv[1]
    host = sys.argv[2]
    try:
        port = int(sys.argv[3])
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        print("Port must be an integer in 1..65535")
        sys.exit(1)

    if not os.path.isfile(filepath):
        print(f"✗ Файл не найден: {filepath}")
        sys.exit(1)

    name = os.path.basename(filepath)
    name_bytes = name.encode("utf-8")
    if len(name_bytes) > MAX_NAME_LEN:
        print(f"✗ Имя файла слишком длинное: {len(name_bytes)} > {MAX_NAME_LEN} байт")
        sys.exit(1)

    size = os.path.getsize(filepath)
    if size > MAX_FILE_SIZE:
        print(f"✗ Файл слишком большой: {size} > {MAX_FILE_SIZE} байт")
        sys.exit(1)

    print(f"→ Отправляю '{name}' ({size} байт) на {host}:{port}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect((host, port))

            # первый пакет: 8 байт - размер файла + имя файла в utf-8
            meta = size.to_bytes(8, "big") + name_bytes     # big-endian - старший байт первый
            send_packet(sock, CMD_UPLOAD_META, meta)

            sent = 0
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)      # читаем кусок с диска
                    if not chunk:
                        break
                    send_packet(sock, CMD_UPLOAD_CHUNK, chunk)      # отправляем чанк данных
                    sent += len(chunk)

            # Конец
            send_packet(sock, CMD_UPLOAD_END)

            # Ответ сервера
            cmd, payload = recv_packet(sock)
            if cmd == CMD_OK:
                print(f"✓ Передача успешна: {sent} байт отправлено")
                return 0
            elif cmd == CMD_ERROR:
                msg = payload.decode("utf-8", errors="replace")
                print(f"✗ Сервер сообщил об ошибке: {msg}")
                return 2
            else:
                print(f"✗ Неожиданный ответ сервера: 0x{cmd:02X}")
                return 2

    except (ConnectionError, OSError) as e:
        print(f"✗ Ошибка соединения: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())