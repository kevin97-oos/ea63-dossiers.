"""Contrôle de la boîte Yahoo expertauto63@yahoo.com — LECTURE SEULE.

Se connecte en IMAP (imap.mail.yahoo.com:993), ouvre chaque dossier avec EXAMINE
(lecture seule) et ne lit que les en-têtes et un court extrait avec BODY.PEEK :
aucun mail n'est supprimé, déplacé ni marqué comme lu.

Écrit mails.json : pour chaque dossier (Envoyés, Boîte de réception, Spam…),
la liste des mails des JOURS derniers jours (date, de, à, cc, objet, extrait).
"""
import email, imaplib, json, os, re, sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

ADRESSE = os.environ.get("YAHOO_EMAIL", "expertauto63@yahoo.com")  # même adresse que l'envoi SMTP
JOURS = int(os.environ.get("JOURS", "7") or 7)
SORTIE = os.environ.get("SORTIE", "mails.json")
EXTRAIT = 300


def dh(v):
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return str(v)


def extrait(raw):
    """Texte court tiré du début du corps (jamais le mail complet)."""
    try:
        m = email.message_from_bytes(raw)
        parts = m.walk() if m.is_multipart() else [m]
        txt, html = "", ""
        for p in parts:
            ct = p.get_content_type()
            if p.get_content_maintype() == "multipart" or p.get_filename():
                continue
            try:
                data = p.get_payload(decode=True) or b""
                s = data.decode(p.get_content_charset() or "utf-8", "replace")
            except Exception:
                continue
            if ct == "text/plain" and not txt:
                txt = s
            elif ct == "text/html" and not html:
                html = re.sub(r"(?is)<(style|script).*?</\1>", " ", s)
                html = re.sub(r"<[^>]+>", " ", html)
        s = txt or html
    except Exception:
        s = raw.decode("utf-8", "replace")
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:EXTRAIT]


def dossiers(M):
    typ, data = M.list()
    out = []
    for l in data or []:
        l = l.decode("utf-8", "replace")
        mm = re.match(r'\((?P<f>[^)]*)\) "?(?P<sep>[^" ]*)"? (?P<n>.+)$', l)
        if not mm:
            continue
        n = mm.group("n").strip()
        if n.startswith('"') and n.endswith('"'):
            n = n[1:-1]
        if "\\Noselect" in mm.group("f"):
            continue
        out.append((n, mm.group("f")))
    return out


def lire(M, nom, depuis):
    typ, _ = M.select('"%s"' % nom, readonly=True)  # EXAMINE : lecture seule
    if typ != "OK":
        return None
    typ, data = M.search(None, "SINCE", depuis.strftime("%d-%b-%Y"))
    ids = (data[0] or b"").split() if typ == "OK" else []
    res = []
    for i in ids[-300:]:
        typ, d = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (DATE FROM TO CC SUBJECT)] BODY.PEEK[TEXT]<0.6000>)")
        if typ != "OK":
            continue
        head, body = b"", b""
        for x in d:
            if isinstance(x, tuple):
                if b"HEADER" in x[0]:
                    head = x[1]
                else:
                    body = x[1]
        h = email.message_from_bytes(head)
        try:
            dt = parsedate_to_datetime(h.get("Date")).astimezone(timezone.utc).isoformat()
        except Exception:
            dt = h.get("Date", "")
        # pour l'extrait on remet les en-têtes de type (multipart) en lisant l'en-tête complet
        typ, hd = M.fetch(i, "(BODY.PEEK[HEADER])")
        full = b""
        for x in hd or []:
            if isinstance(x, tuple):
                full = x[1]
        res.append({"date": dt, "de": dh(h.get("From")), "a": dh(h.get("To")), "cc": dh(h.get("Cc")),
                    "objet": dh(h.get("Subject")), "extrait": extrait(full + b"\r\n" + body)})
    return res


def main():
    pw = os.environ.get("YAHOO_APP_PASSWORD", "")
    if not pw:
        sys.exit("YAHOO_APP_PASSWORD absent")
    depuis = datetime.now(timezone.utc) - timedelta(days=JOURS)
    M = imaplib.IMAP4_SSL("imap.mail.yahoo.com", 993, timeout=90)
    M.login(ADRESSE, pw)
    out = {"adresse": ADRESSE, "depuis": depuis.isoformat(), "genere": datetime.now(timezone.utc).isoformat(), "dossiers": {}}
    for nom, flags in dossiers(M):
        try:
            r = lire(M, nom, depuis)
        except Exception as e:
            r = {"erreur": str(e)[:200]}
        if r is None:
            continue
        out["dossiers"][nom] = r
        print(f"{nom} : {len(r) if isinstance(r, list) else r}")
    M.logout()
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
