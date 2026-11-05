"""极简假 Redis（RESP2），只实现 store.py 用到的命令，供单测使用。"""
import socket
import threading


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.lists = {}
        self.expiry = {}
        self._lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    # ---------- 协议 ----------
    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        fp = conn.makefile("rb")
        try:
            while True:
                args = self._read_command(fp)
                if args is None:
                    return
                reply = self._dispatch(args)
                conn.sendall(self._encode(reply))
        except (OSError, ValueError):
            return
        finally:
            try:
                fp.close()
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _read_command(fp):
        line = fp.readline()
        if not line:
            return None
        if not line.startswith(b"*"):
            raise ValueError("expect array")
        count = int(line[1:-2])
        args = []
        for _ in range(count):
            head = fp.readline()
            if not head.startswith(b"$"):
                raise ValueError("expect bulk")
            size = int(head[1:-2])
            args.append(fp.read(size + 2)[:-2].decode())
        return args

    @staticmethod
    def _encode(value):
        if value is None:
            return b"$-1\r\n"
        if isinstance(value, bool):
            return b":1\r\n" if value else b":0\r\n"
        if isinstance(value, int):
            return f":{value}\r\n".encode()
        if isinstance(value, list):
            out = [f"*{len(value)}\r\n".encode()]
            for item in value:
                blob = str(item).encode()
                out.append(f"${len(blob)}\r\n".encode() + blob + b"\r\n")
            return b"".join(out)
        blob = str(value).encode()
        return f"${len(blob)}\r\n".encode() + blob + b"\r\n"

    # ---------- 命令 ----------
    def _dispatch(self, args):
        cmd = args[0].upper()
        with self._lock:
            if cmd == "PING":
                return "PONG"
            if cmd in ("AUTH", "SELECT"):
                return "OK"
            if cmd == "INCR":
                value = int(self.store.get(args[1], "0")) + 1
                self.store[args[1]] = str(value)
                return value
            if cmd == "PEXPIRE":
                self.expiry[args[1]] = float(args[2])
                return 1
            if cmd == "SET":
                key, value = args[1], args[2]
                if "NX" in args and key in self.store:
                    return None
                self.store[key] = value
                return "OK"
            if cmd == "GET":
                return self.store.get(args[1])
            if cmd == "DEL":
                return 1 if self.store.pop(args[1], None) is not None else 0
            if cmd == "EXISTS":
                return 1 if args[1] in self.store else 0
            if cmd == "RPUSH":
                bucket = self.lists.setdefault(args[1], [])
                bucket.extend(args[2:])
                return len(bucket)
            if cmd == "LTRIM":
                bucket = self.lists.get(args[1], [])
                start, stop = int(args[2]), int(args[3])
                self.lists[args[1]] = bucket[start:] if stop == -1 else bucket[start:stop + 1]
                return "OK"
            if cmd == "LRANGE":
                bucket = self.lists.get(args[1], [])
                start, stop = int(args[2]), int(args[3])
                chunk = bucket[start:] if stop == -1 else bucket[start:stop + 1]
                return list(chunk)
            return None
