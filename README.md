# ◈ SecureChat

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square)
![Version](https://img.shields.io/badge/Version-3.2.1-cyan?style=flat-square)

**Chat di gruppo cifrata end-to-end con architettura client-server.**  
Il server relay non può leggere nessun messaggio. Mai.

[📥 Download](#-download) · [🚀 Avvio rapido](#-avvio-rapido) · [🔒 Come funziona la cifratura](#-come-funziona-la-cifratura) · [☕ Supporta il progetto](#-supporta-il-progetto)

</div>

---

## ✨ Funzionalità

- 🔒 **Cifratura E2E** — RSA-2048 per key exchange, AES-128 (Fernet) per i messaggi
- 💬 **Stanze multiple** — crea stanze pubbliche o protette da password
- 🔕 **Chat privata** — messaggi privati cifrati tra due utenti
- ⚡ **Trill** — notifica sonora con un click
- 😀 **Emoji picker** — 200+ emoji in 7 categorie
- 📣 **@menzioni** — notifica sonora e bubble evidenziato
- 😄 **Reactions** — reagisci ai messaggi con emoji in tempo reale
- 🔔 **Badge notifiche** — contatore messaggi non letti sul titolo finestra
- 🖥️ **UI moderna** — tema scuro con CustomTkinter, font Consolas
- ↻ **Riconnessione automatica** — backoff esponenziale in caso di caduta del server

---

## 🏗️ Architettura

```
┌─────────────┐        TCP :7300        ┌─────────────────────┐
│  Client A   │ ◄──────────────────────► │                     │
│  (Windows)  │                          │   server.py         │
└─────────────┘   Messaggi cifrati E2E   │   (Raspberry Pi)    │
                                          │                     │
┌─────────────┐        TCP :7300        │   Il server vede    │
│  Client B   │ ◄──────────────────────► │   solo blob cifrati │
│  (Windows)  │                          │                     │
└─────────────┘                          └─────────────────────┘
```

Il server fa da **relay puro**: smista i pacchetti senza poterli decifrare.  
Le chiavi RSA non lasciano mai il dispositivo del client.

---

## 📁 Struttura del progetto

```
SecureChat/
├── main.py          # Client — interfaccia grafica (CustomTkinter)
├── client_core.py   # Client — logica di rete e cifratura E2E
├── server.py        # Server relay — da eseguire sul Raspberry Pi (o VPS)
├── LICENSE
└── README.md
```

---

## 📥 Download

### Eseguibile Windows (consigliato)
Scarica l'ultima versione dalla pagina [Releases](https://github.com/massiprofessor/securechat/releases):

- `SecureChat-v3.2.1-win64.zip` — estrai e avvia `SecureChat.exe`
- Non serve installare Python

### Da sorgente
```bash
git clone https://github.com/massiprofessor/securechat.git
cd securechat
pip install customtkinter cryptography
python main.py
```

---

## 🚀 Avvio rapido

### Client (ogni utente)

1. Avvia `SecureChat.exe` (o `python main.py`)
2. Inserisci il tuo **nickname**
3. Inserisci l'indirizzo del server (es. `mioserver.webhop.me`)
4. Clicca **▶ ENTRA**

Sei automaticamente nella stanza **#Generale**.

### Server (chi ospita la chat)

Il server gira su qualsiasi macchina con Python 3.10+.  
La configurazione consigliata è un **Raspberry Pi** con DDNS e port forwarding.

```bash
python server.py --port 7300
```

**Opzioni:**
```
--port    Porta di ascolto (default: 7300)
--log     Abilita log verbosi per debug
```

---

## 🖥️ Configurazione server su Raspberry Pi

Questa è la configurazione usata dallo sviluppatore. È la soluzione più economica per avere un server sempre online.

### Requisiti
- Raspberry Pi (qualsiasi modello con Python 3.10+)
- Connessione internet con IP fisso o DDNS
- Router con supporto port forwarding

### 1. Installa dipendenze
```bash
sudo apt update
sudo apt install python3 python3-pip -y
# Nessun pip install necessario — server.py usa solo librerie standard
```

### 2. Copia il server
```bash
sudo mkdir -p /var/www/html/securechat
sudo cp server.py /var/www/html/securechat/
```

### 3. Configura il firewall
```bash
sudo ufw allow 7300/tcp
sudo ufw reload
```

### 4. Installa come servizio systemd (avvio automatico)
```bash
sudo nano /etc/systemd/system/securechat.service
```

Incolla questo contenuto:
```ini
[Unit]
Description=SecureChat Server v3
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /var/www/html/securechat/server.py --port 7300
Restart=always
RestartSec=5
User=root
WorkingDirectory=/var/www/html/securechat

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now securechat
sudo systemctl status securechat
```

### 5. DDNS (hostname pubblico gratuito)
Se il tuo IP pubblico cambia, usa un servizio DDNS gratuito:

- **No-IP** — [noip.com](https://noip.com) → crea hostname tipo `mioserver.ddns.net`
- **DuckDNS** — [duckdns.org](https://duckdns.org) → più semplice, aggiornamento automatico
- **Dynu** — [dynu.com](https://dynu.com) → alternativa valida

Installa il client DDNS sul Raspberry Pi per aggiornare automaticamente l'IP.

### 6. Port forwarding sul router
Accedi al pannello del router (solitamente `192.168.1.1`) e aggiungi una regola:

| Campo | Valore |
|-------|--------|
| Protocollo | TCP |
| Porta esterna | 7300 |
| Porta interna | 7300 |
| IP destinazione | IP locale del Raspberry Pi |

Trova l'IP locale del Raspberry con:
```bash
hostname -I
```

> 💡 **Consiglio:** assegna un IP fisso al Raspberry Pi dal pannello DHCP del router per evitare che l'IP locale cambi.

### Comandi utili
```bash
sudo systemctl status securechat      # Stato del servizio
sudo systemctl restart securechat     # Riavvia il server
sudo journalctl -u securechat -f      # Log in tempo reale
```

---

## 🔒 Come funziona la cifratura

SecureChat implementa cifratura **end-to-end** a due livelli:

```
1. All'avvio:
   Ogni client genera una coppia di chiavi RSA-2048
   La chiave pubblica viene inviata al server → distribuita a tutti i peer

2. Per ogni messaggio:
   ┌─ Mittente ──────────────────────────────────────────┐
   │  1. Genera session key AES-128 (Fernet) casuale     │
   │  2. Cifra il testo con la session key               │
   │  3. Cifra la session key con RSA pub_key di ogni    │
   │     destinatario                                    │
   │  4. Invia: { ciphertext, encrypted_keys: {id: key} }│
   └─────────────────────────────────────────────────────┘
                          ↓ il server vede solo questo blob
   ┌─ Destinatario ──────────────────────────────────────┐
   │  1. Decifra la propria session key con RSA priv_key │
   │  2. Decifra il testo con la session key             │
   └─────────────────────────────────────────────────────┘
```

**Il server non ha accesso alle chiavi private** — non può decifrare nessun messaggio.

---

## 🔨 Build eseguibile Windows

```bash
pip install pyinstaller
pyinstaller SecureChat.spec
```

L'exe si trova in `dist/SecureChat/SecureChat.exe`.

---

## 📋 Requisiti

| Componente | Requisito |
|------------|-----------|
| Python | 3.10 o superiore |
| customtkinter | `pip install customtkinter` |
| cryptography | `pip install cryptography` |
| tkinter | Incluso in Python standard |
| Sistema operativo | Windows 10/11, Linux, macOS |

Il **server** non ha dipendenze esterne oltre a Python 3.

---

## ☕ Supporta il progetto

Se SecureChat ti è utile, considera una donazione:

[![Ko-fi](https://img.shields.io/badge/Supporta%20su-Ko--fi-FF5E5B?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/massiprofessor)

---

## 👤 Autore

**Mezzina Pasquale Massimo**  
🌐 [massimoesperto.it](https://massimoesperto.it)  
🐙 [github.com/massiprofessor](https://github.com/massiprofessor)

---

## 📄 Licenza

Questo progetto è distribuito sotto licenza **MIT**.  
Vedi il file [LICENSE](LICENSE) per i dettagli.

---

<div align="center">
<sub>Fatto con ❤️ in Italia · SecureChat v3.2.1</sub>
</div>
