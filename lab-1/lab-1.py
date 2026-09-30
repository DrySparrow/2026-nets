#!/usr/bin/env python3
import time
"""
Самообнаружение копий приложения в локальной сети через multicast UDP (Исправленная версия).
"""

import socket
import struct
import sys
import time

PORT            = 8888      # порт multicast-группы
REPORT_INTERVAL = 2.0       # период рассылки report, сек
PEER_TIMEOUT    = 15.0      # таймаут «мёртвой» копии, сек
DISPLAY_INTERVAL = 5.0      # Как часто принудительно обновлять консоль, сек
PAYLOAD_SIZE = 2048

REPORT = b'\x01'
LEAVE  = b'\x02'

REPORT_PKT = REPORT + b'\x00' * (PAYLOAD_SIZE - 1)   # 16 байт
LEAVE_PKT  = LEAVE  + b'\x00' * (PAYLOAD_SIZE - 1)   # 16 байт

def detect_family(addr: str) -> int:
    """Определяем семейство адреса: AF_INET6, если это IPv6, иначе AF_INET."""
    try:
        socket.inet_pton(socket.AF_INET6, addr)
        return socket.AF_INET6
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET, addr)
        return socket.AF_INET
    except OSError:
        raise ValueError(f"Некорректный IPv4/IPv6 адрес: {addr}")


def get_active_local_ip(group: str, family: int) -> str:
    """Определяет IP-адрес активного интерфейса Windows."""
    try:
        s = socket.socket(family, socket.SOCK_DGRAM)
        s.connect((group, PORT))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '::1' if family == socket.AF_INET6 else '127.0.0.1'


class Discovery:
    def __init__(self, group: str):
        self.group     = group.strip()
        self.family    = detect_family(self.group)
        self.ipv6      = (self.family == socket.AF_INET6)
        # Теперь храним узлы как {(ip, port): время_последнего_ответа}
        self.peers     = {}
        self.active_ip = get_active_local_ip(self.group, self.family)

        self.recv_sock = None
        self.send_sock = None
        self.my_port   = 0

        self.last_report = 0.0
        self.last_display = 0.0

    # -------- настройка сокетов -----------------------------------------------
    def setup(self):
        # 1. Сокет для ПРИЕМА (слушает общий порт)
        self.recv_sock = socket.socket(self.family, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Увеличиваем буфер приёма — помогает на Windows избежать WinError 10040
        try:
            self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        except OSError:
            pass

        # 2. Сокет для ОТПРАВКИ (чтобы каждое окно имело свой уникальный порт)
        self.send_sock = socket.socket(self.family, socket.SOCK_DGRAM)

        if self.ipv6:
            try:
                self.recv_sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            except (AttributeError, OSError):
                pass

            self.recv_sock.bind(('::', PORT))
            mreq = socket.inet_pton(socket.AF_INET6, self.group) + struct.pack('@I', 0)
            self.recv_sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)

            self.send_sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_LOOP, 1)
            self.send_sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 2)
        else:
            self.recv_sock.bind(('0.0.0.0', PORT))
            host_bin = socket.inet_aton(self.active_ip)
            group_bin = socket.inet_aton(self.group)

            mreq = struct.pack('4s4s', group_bin, host_bin)
            self.recv_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            self.send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            self.send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            try:
                self.send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, host_bin)
            except OSError:
                pass

        self.recv_sock.settimeout(0.5)

        # Даем системе выделить случайный порт для отправки
        self.send_sock.bind(('', 0) if not self.ipv6 else ('::', 0))
        self.my_port = self.send_sock.getsockname()[1]

    # -------- отправка --------------------------------------------------------
    def send(self, payload: bytes):
        try:
            if self.ipv6:
                self.send_sock.sendto(payload, (self.group, PORT, 0, 0))
            else:
                self.send_sock.sendto(payload, (self.group, PORT))
        except OSError:
            pass

    # -------- основной цикл ---------------------------------------------------
    def run(self):
        self.setup()
        print(f"Запущено на интерфейсе: {self.active_ip}")
        try:
            self.send(REPORT_PKT)
            self.last_report = time.time()
            self.last_display = time.time()
            self._print_peers()

            while True:
                now = time.time()

                # периодический report
                if now - self.last_report >= REPORT_INTERVAL:
                    self.send(REPORT_PKT)
                    self.last_report = now

                changed = False

                # приём пакетов
                try:
                    data, addr = self.recv_sock.recvfrom(4096)
                except socket.timeout:
                    data = None
                except OSError:
                    data = None

                if data:
                    src_ip, src_port = addr[0], addr[1]
                    if self.ipv6 and '%' in src_ip:             # отбрасываем scope-id
                        src_ip = src_ip.split('%', 1)[0]

                    # Игнорируем только СВОИ собственные пакеты (по порту)
                    if src_port != self.my_port:
                        peer_key = (src_ip, src_port)
                        if data[:1] == REPORT:
                            if peer_key not in self.peers:
                                changed = True
                            self.peers[peer_key] = now
                        elif data[:1] == LEAVE:
                            if peer_key in self.peers:
                                del self.peers[peer_key]
                                changed = True

                # истечение «мёртвых» копий
                for key in list(self.peers.keys()):
                    if now - self.peers[key] > PEER_TIMEOUT:
                        del self.peers[key]
                        changed = True

                # Печатаем при любом изменении ИЛИ если прошло 5 секунд
                if changed or (now - self.last_display >= DISPLAY_INTERVAL):
                    self._print_peers()
                    self.last_display = now

        except KeyboardInterrupt:
            pass
        finally:
            for _ in range(3):
                self.send(LEAVE)
                time.sleep(0.01)
            self._teardown()

    # -------- утилиты ---------------------------------------------------------
    def _print_peers(self):
        t = time.strftime("%H:%M:%S")
        now = time.time()

        # Заголовок таблицы
        header = f"{'IP-адрес':<40} {'Порт':>6} {'Последний report':>17} {'Возраст, с':>10}"
        sep = "-" * len(header)

        print(f"\n[{t}] Живые копии: {len(self.peers)}")
        print(sep)
        print(header)
        print(sep)

        if not self.peers:
            print(f"{'(нет активных копий)':<40}")
        else:
            # Сортируем по IP, затем по порту
            for (ip, port), last_seen in sorted(self.peers.items()):
                last_str = time.strftime("%H:%M:%S", time.localtime(last_seen))
                age = now - last_seen
                print(f"{ip:<40} {port:>6} {last_str:>17} {age:>10.1f}")

        print(sep)

    def _teardown(self):
        try:
            if self.ipv6:
                mreq = socket.inet_pton(socket.AF_INET6, self.group) + struct.pack('@I', 0)
                self.recv_sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_LEAVE_GROUP, mreq)
            else:
                host_bin = socket.inet_aton(self.active_ip)
                group_bin = socket.inet_aton(self.group)
                mreq = struct.pack('4s4s', group_bin, host_bin)
                self.recv_sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
        except OSError:
            pass
        finally:
            self.recv_sock.close()
            self.send_sock.close()


def main():
    if len(sys.argv) != 2:
        print(f"Использование: {sys.argv[0]} <multicast-group>")
        print("Примеры:")
        print("    IPv4: 239.255.0.1")
        print("    IPv6: ff02::114")
        sys.exit(1)
    Discovery(sys.argv[1]).run()


if __name__ == '__main__':
    main()
