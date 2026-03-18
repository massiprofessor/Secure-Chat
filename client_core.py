"""
SecureChat — Client Core
========================
Gestisce connessione al server, cifratura E2E e stato locale.
Importato da main.py (UI).
"""

import socket, threading, json, struct, time, os, hashlib, base64, logging
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend

# ── Tipi messaggio (mirrors server.py) ───────────────────────
T_HELLO       = "HELLO"
T_HELLO_ACK   = "HELLO_ACK"
T_PEER_JOIN   = "PEER_JOIN"
T_PEER_LEAVE  = "PEER_LEAVE"
T_MSG         = "MSG"
T_PRIVATE     = "PRIVATE"
T_KEY_REQ     = "KEY_REQ"
T_KEY_RESP    = "KEY_RESP"
T_ROOM_CREATE = "ROOM_CREATE"
T_ROOM_JOIN   = "ROOM_JOIN"
T_ROOM_LEAVE  = "ROOM_LEAVE"
T_ROOM_LIST   = "ROOM_LIST"
T_TYPING      = "TYPING"
T_TYPING_STOP = "TYPING_STOP"
T_PING        = "PING"
T_PONG        = "PONG"
T_ERROR       = "ERROR"
T_REACTION    = "REACTION"

MAX_MSG_SIZE  = 1 * 1024 * 1024


# ── Cifratura E2E ────────────────────────────────────────────
class E2ECrypto:
    """
    Ogni client ha coppia RSA-2048 propria.
    Per cifrare un messaggio verso N peer:
      1. Genera session_key Fernet (AES-128)
      2. Cifra il testo con session_key
      3. Cifra session_key con RSA pub_key di ogni destinatario
      4. Invia: {ct: <blob>, keys: {client_id: <rsa_enc_key>}}
    Il server vede solo blob opachi.
    """
    def __init__(self):
        self._priv = rsa.generate_private_key(65537, 2048, default_backend())
        self._pub  = self._priv.public_key()
        self._peer_keys: dict[str, bytes] = {}  # client_id → pub_key PEM bytes

    def pub_pem(self) -> str:
        return self._pub.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    def set_peer_key(self, client_id: str, pub_pem: str):
        self._peer_keys[client_id] = pub_pem.encode() if isinstance(pub_pem, str) else pub_pem

    def remove_peer(self, client_id: str):
        self._peer_keys.pop(client_id, None)

    def peer_ids(self) -> list[str]:
        return list(self._peer_keys.keys())

    def encrypt_for(self, text: str, recipient_ids: list[str]) -> dict | None:
        """Cifra text per una lista di destinatari. Ritorna il payload o None."""
        session_key = Fernet.generate_key()
        f = Fernet(session_key)
        ct = f.encrypt(text.encode("utf-8"))
        keys = {}
        for rid in recipient_ids:
            pem = self._peer_keys.get(rid)
            if not pem: continue
            try:
                pub = serialization.load_pem_public_key(pem, backend=default_backend())
                enc = pub.encrypt(session_key, padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
                keys[rid] = base64.b64encode(enc).decode()
            except: pass
        if not keys: return None
        return {
            "ct":   base64.b64encode(ct).decode(),
            "keys": keys,
        }

    def decrypt(self, my_id: str, payload: dict) -> str | None:
        """Decifra un payload ricevuto. Ritorna il testo o None."""
        try:
            enc_key = base64.b64decode(payload["keys"][my_id])
            session_key = self._priv.decrypt(enc_key, padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
            ct = base64.b64decode(payload["ct"])
            return Fernet(session_key).decrypt(ct).decode("utf-8")
        except:
            return None


# ── Utilità pacchetti ────────────────────────────────────────
def send_pkt(sock: socket.socket, msg_type: str, payload: dict) -> bool:
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


# ── Client ───────────────────────────────────────────────────
class ChatClient:
    """
    Connessione al server e gestione messaggi.
    `on_event(event_type, data)` è chiamato dal thread di rete —
    l'UI deve fare `self.after(0, ...)` prima di toccare i widget.
    """

    def __init__(self, nick: str, server_host: str, server_port: int, on_event):
        self.nick        = nick
        self.server_host = server_host
        self.server_port = server_port
        self.on_event    = on_event
        self.crypto      = E2ECrypto()
        self.client_id   = ""
        self._sock: socket.socket | None = None
        self._running    = False
        self._send_lock  = threading.Lock()

        # Stato locale
        self.peers:  dict[str, dict]  = {}   # client_id → {nick, pub_key}
        self.rooms:  dict[str, dict]  = {}   # name → {protected, members, joined}
        self.my_rooms: set[str]       = set()

    # ── Connessione ──────────────────────────────────────────
    def connect(self, timeout: float = 10.0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect((self.server_host, self.server_port))
        self._sock.settimeout(None)
        # Handshake
        send_pkt(self._sock, T_HELLO, {
            "nick":    self.nick,
            "pub_key": self.crypto.pub_pem(),
        })
        pkt = recv_pkt(self._sock)
        if not pkt or pkt.get("type") != T_HELLO_ACK:
            raise ConnectionError("Handshake fallito")
        self.client_id = pkt["client_id"]
        # Registra peer già connessi
        for p in pkt.get("peers", []):
            self._register_peer(p)
        # Registra stanze disponibili
        for r in pkt.get("rooms", []):
            self.rooms[r["name"]] = r
        self.my_rooms.add("Generale")
        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        logging.info(f"[CLIENT] Connesso come {self.nick} (id={self.client_id})")

    def disconnect(self):
        self._running = False
        try: self._sock.close()
        except: pass

    def reconnect(self, max_retries: int = 10):
        """Riconnessione automatica con backoff."""
        for attempt in range(max_retries):
            wait = min(2 ** attempt, 60)
            logging.info(f"[CLIENT] Riconnessione tra {wait}s (tentativo {attempt+1})")
            self.on_event("reconnecting", {"attempt": attempt + 1, "wait": wait})
            time.sleep(wait)
            try:
                self.connect(timeout=10.0)
                self.on_event("reconnected", {})
                return
            except Exception as e:
                logging.warning(f"[CLIENT] Riconnessione fallita: {e}")
        self.on_event("error", {"msg": "Impossibile riconnettersi al server"})

    # ── Loop ricezione ───────────────────────────────────────
    def _recv_loop(self):
        while self._running:
            pkt = recv_pkt(self._sock)
            if pkt is None:
                if self._running:
                    self.on_event("disconnected", {})
                    threading.Thread(target=self.reconnect, daemon=True).start()
                break
            try:
                self._handle(pkt)
            except Exception as e:
                logging.warning(f"[CLIENT] Errore dispatch: {e}")

    def _handle(self, pkt: dict):
        t = pkt.get("type")

        if t == T_PING:
            self._send_raw(T_PONG, {})

        elif t == T_PEER_JOIN:
            self._register_peer(pkt)
            self.on_event("peer_joined", {
                "client_id": pkt["client_id"],
                "nick":      pkt["nick"],
                "room":      pkt.get("room", "Generale"),
            })

        elif t == T_PEER_LEAVE:
            cid = pkt["client_id"]
            self.peers.pop(cid, None)
            self.crypto.remove_peer(cid)
            self.on_event("peer_left", {
                "client_id": cid, "nick": pkt.get("nick", cid),
            })

        elif t == T_MSG:
            from_id   = pkt["from_id"]
            from_nick = pkt["from_nick"]
            room      = pkt.get("room", "Generale")
            payload   = pkt.get("payload", {})
            # Decifra con la nostra chiave privata
            if isinstance(payload, dict) and "keys" in payload:
                text = self.crypto.decrypt(self.client_id, payload)
                if text is None:
                    text = "[messaggio cifrato — chiave non disponibile]"
            else:
                text = str(payload)
            self.on_event("message", {
                "from_id": from_id, "from_nick": from_nick,
                "room": room, "text": text, "ts": pkt.get("ts", time.time()),
            })

        elif t == T_PRIVATE:
            from_id   = pkt["from_id"]
            from_nick = pkt["from_nick"]
            payload   = pkt.get("payload", {})
            if isinstance(payload, dict) and "keys" in payload:
                text = self.crypto.decrypt(self.client_id, payload)
                if text is None: text = "[messaggio privato cifrato — chiave non disponibile]"
            else:
                text = str(payload)
            self.on_event("private_message", {
                "from_id": from_id, "from_nick": from_nick,
                "text": text, "ts": pkt.get("ts", time.time()),
            })

        elif t == T_KEY_RESP:
            cid  = pkt["client_id"]
            pem  = pkt.get("pub_key", "")
            nick = pkt.get("nick", cid)
            if pem:
                self.crypto.set_peer_key(cid, pem)
                self.peers[cid] = {"nick": nick, "pub_key": pem}
            self.on_event("key_received", {"client_id": cid, "nick": nick})

        elif t == T_ROOM_LIST:
            for r in pkt.get("rooms", []):
                self.rooms[r["name"]] = r
            joined = pkt.get("joined")
            if joined:
                self.my_rooms.add(joined)
                for m in pkt.get("members", []):
                    self._register_peer(m)
            self.on_event("room_list", {
                "rooms": list(self.rooms.values()), "joined": joined,
            })

        elif t == T_TYPING:
            self.on_event("typing", {
                "from_id": pkt["from_id"], "nick": pkt["nick"], "room": pkt.get("room"),
            })

        elif t == T_TYPING_STOP:
            self.on_event("typing_stop", {
                "from_id": pkt["from_id"], "room": pkt.get("room"),
            })

        elif t == T_REACTION:
            self.on_event("reaction", {
                "from_id":   pkt["from_id"],
                "from_nick": pkt["from_nick"],
                "room":      pkt.get("room", "Generale"),
                "msg_ts":    pkt.get("msg_ts", ""),
                "emoji":     pkt.get("emoji", ""),
            })

        elif t == T_ERROR:
            self.on_event("error", {"msg": pkt.get("msg", "Errore sconosciuto")})

    def _register_peer(self, p: dict):
        cid = p.get("client_id", "")
        if not cid or cid == self.client_id: return
        self.peers[cid] = {"nick": p.get("nick", cid), "pub_key": p.get("pub_key", "")}
        if p.get("pub_key"):
            self.crypto.set_peer_key(cid, p["pub_key"])

    # ── Invio messaggi ───────────────────────────────────────
    def _send_raw(self, msg_type: str, payload: dict):
        with self._send_lock:
            send_pkt(self._sock, msg_type, payload)

    def send_message(self, room: str, text: str):
        """Cifra E2E il messaggio per tutti i peer nella stanza e invia."""
        # Trova destinatari: peer che sono in questa stanza
        # (il server smista solo ai membri — il client cifra per tutti i peer noti)
        recipient_ids = self.crypto.peer_ids()
        if not recipient_ids:
            # Nessun peer connesso — invia plaintext placeholder
            # (non verrà mostrato a nessuno comunque)
            self._send_raw(T_MSG, {"room": room, "payload": {}})
            return
        payload = self.crypto.encrypt_for(text, recipient_ids)
        if payload is None:
            self.on_event("error", {"msg": "Cifratura fallita"}); return
        self._send_raw(T_MSG, {"room": room, "payload": payload})

    def send_private(self, to_id: str, text: str):
        """Cifra E2E e invia messaggio privato."""
        pem = self.peers.get(to_id, {}).get("pub_key")
        if not pem:
            # Richiedi chiave pubblica al server
            self._send_raw(T_KEY_REQ, {"target_id": to_id})
            self.on_event("error", {"msg": "Chiave peer non disponibile — riprova tra un momento"})
            return
        payload = self.crypto.encrypt_for(text, [to_id])
        if payload is None:
            self.on_event("error", {"msg": "Cifratura fallita"}); return
        self._send_raw(T_PRIVATE, {"to_id": to_id, "payload": payload})

    def create_room(self, name: str, password: str = ""):
        room_hash = hashlib.sha256(password.encode()).hexdigest() if password else ""
        self._send_raw(T_ROOM_CREATE, {
            "room": name, "protected": bool(password), "room_hash": room_hash,
        })

    def join_room(self, name: str, password: str = ""):
        room_hash = hashlib.sha256(password.encode()).hexdigest() if password else ""
        self._send_raw(T_ROOM_JOIN, {"room": name, "room_hash": room_hash})

    def leave_room(self, name: str):
        self._send_raw(T_ROOM_LEAVE, {"room": name})
        self.my_rooms.discard(name)

    def send_typing(self, room: str):
        self._send_raw(T_TYPING, {"room": room})

    def send_typing_stop(self, room: str):
        self._send_raw(T_TYPING_STOP, {"room": room})

    def send_reaction(self, room: str, msg_ts: str, emoji: str):
        """Invia una reaction a un messaggio identificato dal suo timestamp."""
        self._send_raw(T_REACTION, {"room": room, "msg_ts": msg_ts, "emoji": emoji})

    def peers_in_room(self, room_name: str) -> list[dict]:
        """Lista peer connessi (tutti — il server gestisce la membership)."""
        return [
            {"client_id": cid, **info}
            for cid, info in self.peers.items()
        ]