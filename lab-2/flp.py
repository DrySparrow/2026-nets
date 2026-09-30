import struct

CMD_UPLOAD_META  = 0x01
CMD_UPLOAD_CHUNK = 0x02
CMD_UPLOAD_END   = 0x03
CMD_OK           = 0x04
CMD_ERROR        = 0x05

HEADER = struct.Struct("!BI")
CHUNK_SIZE     = 64 * 1024          # 64 КБ — компромисс между оверхедом и памятью
MAX_FILE_SIZE  = 1 << 40            # 1 ТБ
MAX_NAME_LEN   = 4096               # байт UTF-8


# добавляет в начало сообщения 5 байт
def send_packet(sock, cmd: int, payload: bytes = b""):
    # 1б- норер команды, 4б - длина сообщения, остальное само сообщение
    sock.sendall(HEADER.pack(cmd, len(payload)) + payload)


def recv_exact(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:    # пока не набрали нужное количество
        chunk = sock.recv(n - len(data))    # просим ровно столько, сколько не хватает
        if not chunk:
            raise ConnectionError("Соединение закрыто")
        data += chunk
    return data


def recv_packet(sock):
    header = recv_exact(sock, HEADER.size)      # читаем заголовок пакета
    cmd, length = HEADER.unpack(header)
    payload = recv_exact(sock, length) if length else b""   # читаем сам текст сообщения
    return cmd, payload