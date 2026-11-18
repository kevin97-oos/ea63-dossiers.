#!/usr/bin/env python3
"""Envoi des dossiers aux assurances demandé à Jeffrey – Expert Auto 63.

L'application dépose chaque demande d'envoi dans l'espace en ligne (meta/mail_<id>,
avec le PDF découpé en meta/mailpart_<id>_<n>). Ce script, lancé toutes les
5 minutes par GitHub, envoie le mail depuis expertauto63@yahoo.com avec le PDF
en pièce jointe (copie au garage), puis passe le dossier en « Envoyé ».
Secrets : APP_EMAIL / APP_PASSWORD, YAHOO_APP_PASSWORD. DRY_RUN=1 pour un essai.
"""
import base64, os, smtplib, ssl, sys, urllib.parse
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relancer import http, connexion, lister, ecrire, BASE, EXPEDITEUR  # noqa: E402

DRY = os.environ.get("DRY_RUN", "") in ("1", "true", "oui")

def mails(t):
    return [x.strip() for x in str(t or "").replace(";", ",").split(",") if "@" in x]

def main():
    token = connexion()
    meta = lister("meta", token)
    demandes = {k: (v.get("v") or {}) for k, v in meta.items() if k.startswith("mail_")}
    attente = {k: v for k, v in demandes.items() if isinstance(v, dict) and v.get("statut") == "attente"}
    print(f"{len(attente)} envoi(s) en attente" + (" – ESSAI" if DRY else ""))
    if not attente:
        return
    smtp = None
    dossiers = lister("dossiers", token)
    erreurs = 0
    for k, v in sorted(attente.items(), key=lambda kv: kv[1].get("demande", "")):
        mid = k[len("mail_"):]
        lib = f"N° {v.get('numero') or '?'} {v.get('client', '')}"
        try:
            to, cc = mails(v.get("to")), mails(v.get("cc"))
            if not to:
                raise RuntimeError("aucune adresse de destinataire")
            n = int(v.get("parts") or 0)
            b64 = "".join(str((meta.get(f"mailpart_{mid}_{i}") or {}).get("v") or "") for i in range(n))
            pdf = base64.b64decode(b64) if b64 else b""
            if n and (not pdf.startswith(b"%PDF")):
                raise RuntimeError("PDF incomplet (dépôt interrompu ?)")
            m = EmailMessage()
            m["From"] = formataddr(("EXPERT AUTO 63", EXPEDITEUR))
            m["To"] = ", ".join(to)
            if cc:
                m["Cc"] = ", ".join(cc)
            m["Subject"] = v.get("objet") or "Dossier sinistre – EXPERT AUTO 63"
            m["Date"] = formatdate(localtime=True)
            m.set_content(v.get("corps") or "")
            if pdf:
                m.add_attachment(pdf, maintype="application", subtype="pdf", filename=v.get("fichier") or "DOSSIER.pdf")
            if not DRY:
                if smtp is None:
                    smtp = smtplib.SMTP_SSL("smtp.mail.yahoo.com", 465, context=ssl.create_default_context(), timeout=90)
                    smtp.login(EXPEDITEUR, os.environ["YAHOO_APP_PASSWORD"])
                smtp.send_message(m, from_addr=EXPEDITEUR, to_addrs=to + cc + [EXPEDITEUR])
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            print(f"  ENVOYÉ  {lib} -> {', '.join(to + cc)} ({len(pdf) // 1024} Ko)")
            if DRY:
                continue
            v2 = dict(v, statut="envoye", envoye=now)
            ecrire("meta", k, {"v": v2}, token)
            for i in range(n):  # on vide les morceaux du PDF
                ecrire("meta", f"mailpart_{mid}_{i}", {"v": ""}, token)
            d = dossiers.get(v.get("dossier") or "")
            if d:
                hist = (d.get("historique") or []) + [{"at": now, "by": "Jeffrey (envoi automatique)",
                                                         "txt": f"Envoyé à l'assurance par mail ({', '.join(to + cc)}) : {v.get('fichier')}"}]
                maj = {"historique": hist, "updatedAt": now, "updatedBy": "Jeffrey (envoi automatique)",
                       "envoi": {"at": now, "by": v.get("par") or "Jeffrey", "to": ", ".join(to)}}
                if d.get("statut") not in ("paye",):
                    maj["statut"] = "envoye"
                ecrire("dossiers", v["dossier"], maj, token)
        except Exception as e:  # noqa
            erreurs += 1
            print(f"  ERREUR  {lib} : {e}")
            if not DRY:
                try:
                    ecrire("meta", k, {"v": dict(v, statut="erreur", erreur=str(e)[:300])}, token)
                except Exception as e2:  # noqa
                    print("    (statut non enregistré :", e2, ")")
    if smtp:
        smtp.quit()
    if erreurs:
        sys.exit(1)

if __name__ == "__main__":
    main()
