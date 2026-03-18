"""
SecureChat — Server Relay
=========================
Da eseguire sul Raspberry Pi.
I messaggi sono cifrati E2E tra i client: il server non può leggerli.

Porta TCP: 7300 (configurabile con --port)

Uso:
    python3 server.py [--port 7300] [--log]

systemd:
    ExecStart=/usr/bin/python3 /var/www/html/securechat/server.py --port 7300
"""

import socket, threading, json, struct, time, argparse, logging, hashlib, os
from datetime import datetime

VERSION      = "3.0.0"
DEFAULT_PORT = 7300
MAX_CLIENTS  = 200
PING_INTERVAL = 30   # secondi tra ping server→client
PING_TIMEOUT  = 90   # secondi: client rimosso se non risponde
MAX_MSG_SIZE  = 1 * 1024 * 1024  # 1 MB massimo per messaggio

# ── Tipi messaggio ────────────────────────────────────────────
T_HELLO       = "HELLO"        # client → server: {nick, pub_key}
T_HELLO_ACK   = "HELLO_ACK"   # server → client: {client_id, peers: [{id, nick, pub_key}]}
T_PEER_JOIN   = "PEER_JOIN"   # server → tutti: {client_id, nick, pub_key}
T_PEER_LEAVE  = "PEER_LEAVE"  # server → tutti: {client_id, nick}
T_MSG         = "MSG"          # client → server → destinatari: {room, payload (cifrato)}
T_PRIVATE     = "PRIVATE"     # client → server → destinatario: {to_id, payload (cifrato)}
T_KEY_REQ     = "KEY_REQ"     # client A → server → client B: richiesta chiave pubblica
T_KEY_RESP    = "KEY_RESP"    # client B → server → client A: risposta con pub_key
T_ROOM_CREATE = "ROOM_CREATE" # client → server: {room, protected, room_hash}
T_ROOM_JOIN   = "ROOM_JOIN"   # client → server: {room, room_hash}
T_ROOM_LEAVE  = "ROOM_LEAVE"  # client → server: {room}
T_ROOM_LIST   = "ROOM_LIST"   # server → client: {rooms: [{name, protected, members}]}
T_TYPING      = "TYPING"      # client → server → stanza: {room, nick}
T_TYPING_STOP = "TYPING_STOP"
T_PING        = "PING"
T_PONG        = "PONG"
T_ERROR       = "ERROR"        # server → client: {msg}
T_REACTION    = "REACTION"     # client → server → stanza: {room, msg_ts, from_nick, emoji}

# ── Utilità pacchetti ─────────────────────────────────────────
def send_pkt(sock: socket.socket, msg_type: str, payload: dict):
    data = json.dumps({"type": msg_type, **payload}).encode("utf-8")
    try:
        sock.sendall(struct.pack("!I", len(data)) + data)
        return True
    except:
        return False

def recv_pkt(sock: socket.socket) -> dict | None:
    try:
        hdr = b""
        while len(hdr) < 4:
            c = sock.recv(4 - len(hdr))
            if not c: return None
            hdr += c
        ml = struct.unpack("!I", hdr)[0]
        if ml > MAX_MSG_SIZE: return None
        raw = b""
        while len(raw) < ml:
            c = sock.recv(ml - len(raw))
            if not c: return None
            raw += c
        return json.loads(raw.decode("utf-8"))
    except:
        return None


# ── Client connesso ───────────────────────────────────────────
class Client:
    def __init__(self, sock: socket.socket, addr: tuple, client_id: str):
        self.sock       = sock
        self.addr       = addr
        self.client_id  = client_id
        self.nick       = ""
        self.pub_key    = ""
        self.rooms: set[str] = set()
        self.last_seen  = time.time()
        self.lock       = threading.Lock()

    def send(self, msg_type: str, payload: dict) -> bool:
        with self.lock:
            return send_pkt(self.sock, msg_type, payload)

    def touch(self): self.last_seen = time.time()


# ── Stanza ────────────────────────────────────────────────────
class Room:
    def __init__(self, name: str, protected: bool, room_hash: str, owner_id: str):
        self.name      = name
        self.protected = protected
        self.room_hash = room_hash  # SHA256 password — server controlla solo hash
        self.owner_id  = owner_id
        self.members: set[str] = set()  # client_id
        self.created_at = time.time()


# ── Server ────────────────────────────────────────────────────
class ChatServer:
    def __init__(self, port: int, verbose: bool):
        self.port    = port
        self.verbose = verbose
        self._running = False
        self._clients: dict[str, Client] = {}   # client_id → Client
        self._rooms:   dict[str, Room]   = {}   # room_name → Room
        self._lock     = threading.Lock()
        self._stats    = {"connections": 0, "messages": 0, "rooms_created": 0}
        # Crea la stanza Generale di default (non protetta)
        self._rooms["Generale"] = Room("Generale", False, "", "server")

    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(MAX_CLIENTS)
        srv.settimeout(1.0)
        self._running = True
        logging.info(f"SecureChat Server v{VERSION} — porta {self.port}")
        logging.info(f"Max client: {MAX_CLIENTS}  |  Ping ogni {PING_INTERVAL}s")
        threading.Thread(target=self._ping_loop,  daemon=True).start()
        threading.Thread(target=self._stats_loop, daemon=True).start()
        try:
            while self._running:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                with self._lock:
                    if len(self._clients) >= MAX_CLIENTS:
                        send_pkt(conn, T_ERROR, {"msg": "Server pieno"}); conn.close(); continue
                client_id = hashlib.sha1(os.urandom(16)).hexdigest()[:16]
                client = Client(conn, addr, client_id)
                threading.Thread(target=self._handle_client,
                                 args=(client,), daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            srv.close()
            logging.info("Server arrestato")

    # ── Gestione client ───────────────────────────────────────
    def _handle_client(self, client: Client):
        self._stats["connections"] += 1
        try:
            client.sock.settimeout(15)
            pkt = recv_pkt(client.sock)
            if not pkt or pkt.get("type") != T_HELLO:
                client.sock.close(); return
            nick    = str(pkt.get("nick", ""))[:32].strip()
            pub_key = str(pkt.get("pub_key", ""))
            if not nick or not pub_key:
                client.sock.close(); return
            client.nick    = nick
            client.pub_key = pub_key
            client.sock.settimeout(None)
            client.touch()

            with self._lock:
                self._clients[client.client_id] = client
                # Aggiungi automaticamente a Generale
                self._rooms["Generale"].members.add(client.client_id)
                client.rooms.add("Generale")
                # Lista peer già connessi (per key exchange E2E)
                peers = [
                    {"client_id": c.client_id, "nick": c.nick, "pub_key": c.pub_key}
                    for cid, c in self._clients.items()
                    if cid != client.client_id
                ]
                room_list = self._get_room_list()

            # Conferma connessione al nuovo client
            client.send(T_HELLO_ACK, {
                "client_id": client.client_id,
                "peers": peers,
                "rooms": room_list,
            })

            # Notifica tutti dell'arrivo
            self._broadcast(T_PEER_JOIN, {
                "client_id": client.client_id,
                "nick": nick, "pub_key": pub_key,
            }, exclude=client.client_id)

            logging.info(f"[+] {nick} ({client.addr[0]}) — id={client.client_id}")

            # Loop messaggi
            while self._running:
                pkt = recv_pkt(client.sock)
                if pkt is None: break
                client.touch()
                self._dispatch(client, pkt)

        except Exception as e:
            if self.verbose: logging.debug(f"Client {client.nick}: {e}")
        finally:
            self._disconnect(client)

    def _dispatch(self, client: Client, pkt: dict):
        t = pkt.get("type")

        if t == T_PONG:
            pass  # aggiornato da touch()

        elif t == T_MSG:
            self._stats["messages"] += 1
            room_name = pkt.get("room", "Generale")
            with self._lock:
                room = self._rooms.get(room_name)
                if not room or client.client_id not in room.members: return
                members = set(room.members)
            # Relay a tutti i membri della stanza (tranne il mittente)
            payload = {
                "from_id":   client.client_id,
                "from_nick": client.nick,
                "room":      room_name,
                "payload":   pkt.get("payload", ""),  # blob cifrato — server non legge
                "ts":        time.time(),
            }
            for mid in members:
                if mid != client.client_id:
                    with self._lock:
                        c = self._clients.get(mid)
                    if c: c.send(T_MSG, payload)

        elif t == T_PRIVATE:
            to_id = pkt.get("to_id", "")
            with self._lock:
                target = self._clients.get(to_id)
            if target:
                target.send(T_PRIVATE, {
                    "from_id":   client.client_id,
                    "from_nick": client.nick,
                    "payload":   pkt.get("payload", ""),
                    "ts":        time.time(),
                })

        elif t == T_KEY_REQ:
            # A chiede la chiave pubblica di B per cifrare E2E
            target_id = pkt.get("target_id", "")
            with self._lock:
                target = self._clients.get(target_id)
            if target:
                client.send(T_KEY_RESP, {
                    "client_id": target_id,
                    "nick":      target.nick,
                    "pub_key":   target.pub_key,
                })

        elif t == T_ROOM_CREATE:
            name      = str(pkt.get("room", ""))[:32].strip()
            protected = bool(pkt.get("protected", False))
            room_hash = str(pkt.get("room_hash", ""))
            if not name: return
            with self._lock:
                if name in self._rooms:
                    client.send(T_ERROR, {"msg": f"Stanza '{name}' esiste già"}); return
                room = Room(name, protected, room_hash, client.client_id)
                room.members.add(client.client_id)
                client.rooms.add(name)
                self._rooms[name] = room
                self._stats["rooms_created"] += 1
                room_list = self._get_room_list()
            # Notifica tutti della nuova stanza
            self._broadcast(T_ROOM_LIST, {"rooms": room_list})
            logging.info(f"[ROOM] '{name}' creata da {client.nick}")

        elif t == T_ROOM_JOIN:
            name      = str(pkt.get("room", ""))
            room_hash = str(pkt.get("room_hash", ""))
            with self._lock:
                room = self._rooms.get(name)
                if not room:
                    client.send(T_ERROR, {"msg": f"Stanza '{name}' non trovata"}); return
                if room.protected and room.room_hash != room_hash:
                    client.send(T_ERROR, {"msg": "Password errata"}); return
                if client.client_id in room.members: return
                room.members.add(client.client_id)
                client.rooms.add(name)
                members_info = [
                    {"client_id": c.client_id, "nick": c.nick, "pub_key": c.pub_key}
                    for cid in room.members
                    if cid != client.client_id
                    for c in [self._clients.get(cid)] if c
                ]
            client.send(T_ROOM_LIST, {
                "rooms":   self._get_room_list(),
                "joined":  name,
                "members": members_info,
            })
            # Notifica altri membri
            for mid in list(room.members):
                if mid != client.client_id:
                    with self._lock:
                        c = self._clients.get(mid)
                    if c:
                        c.send(T_PEER_JOIN, {
                            "client_id": client.client_id,
                            "nick": client.nick, "pub_key": client.pub_key,
                            "room": name,
                        })

        elif t == T_ROOM_LEAVE:
            name = str(pkt.get("room", ""))
            if name == "Generale": return  # non si può lasciare Generale
            with self._lock:
                room = self._rooms.get(name)
                if room: room.members.discard(client.client_id)
                client.rooms.discard(name)

        elif t == T_TYPING:
            room_name = pkt.get("room", "Generale")
            self._broadcast_room(T_TYPING, {
                "from_id": client.client_id, "nick": client.nick, "room": room_name,
            }, room_name, exclude=client.client_id)

        elif t == T_TYPING_STOP:
            room_name = pkt.get("room", "Generale")
            self._broadcast_room(T_TYPING_STOP, {
                "from_id": client.client_id, "room": room_name,
            }, room_name, exclude=client.client_id)

        elif t == T_REACTION:
            room_name = pkt.get("room", "Generale")
            self._broadcast_room(T_REACTION, {
                "from_id":   client.client_id,
                "from_nick": client.nick,
                "room":      room_name,
                "msg_ts":    pkt.get("msg_ts", ""),   # timestamp del messaggio target
                "emoji":     pkt.get("emoji", ""),
            }, room_name, exclude=client.client_id)

    def _disconnect(self, client: Client):
        with self._lock:
            self._clients.pop(client.client_id, None)
            for room in self._rooms.values():
                room.members.discard(client.client_id)
        try: client.sock.close()
        except: pass
        self._broadcast(T_PEER_LEAVE, {
            "client_id": client.client_id, "nick": client.nick,
        })
        logging.info(f"[-] {client.nick} disconnesso")

    # ── Broadcast ─────────────────────────────────────────────
    def _broadcast(self, msg_type: str, payload: dict, exclude: str = ""):
        with self._lock:
            targets = list(self._clients.values())
        for c in targets:
            if c.client_id != exclude:
                c.send(msg_type, payload)

    def _broadcast_room(self, msg_type: str, payload: dict,
                        room_name: str, exclude: str = ""):
        with self._lock:
            room = self._rooms.get(room_name)
            if not room: return
            members = list(room.members)
        for mid in members:
            if mid != exclude:
                with self._lock:
                    c = self._clients.get(mid)
                if c: c.send(msg_type, payload)

    def _get_room_list(self) -> list:
        return [
            {"name": r.name, "protected": r.protected,
             "members": len(r.members), "owner": r.owner_id}
            for r in self._rooms.values()
        ]

    # ── Ping loop ─────────────────────────────────────────────
    def _ping_loop(self):
        while self._running:
            time.sleep(PING_INTERVAL)
            now = time.time()
            with self._lock:
                all_clients = list(self._clients.values())
            dead = []
            for c in all_clients:
                if now - c.last_seen > PING_TIMEOUT:
                    dead.append(c)
                else:
                    c.send(T_PING, {})
            for c in dead:
                logging.info(f"[TIMEOUT] {c.nick} rimosso (inattivo)")
                self._disconnect(c)

    # ── Stats loop ────────────────────────────────────────────
    def _stats_loop(self):
        while self._running:
            time.sleep(300)
            with self._lock:
                nc = len(self._clients); nr = len(self._rooms)
            logging.info(
                f"[STATS] client={nc} stanze={nr} "
                f"connessioni={self._stats['connections']} "
                f"messaggi={self._stats['messages']}"
            )


# ── Entry point ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="SecureChat Server v3")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.log else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ChatServer(port=args.port, verbose=args.log).start()

if __name__ == "__main__":
    main()


