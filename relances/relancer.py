#!/usr/bin/env python3
"""Relances automatiques des assurances – Expert Auto 63.

Tous les jours, ce script lit les dossiers de l'application EA63 Dossiers
(Firestore) et relance par mail l'assurance de chaque dossier
« Envoyé à l'assurance » qui n'est pas encore payé, si la dernière relance
(ou l'envoi du dossier) date d'au moins RELANCE_JOURS jours.

Secrets GitHub nécessaires :
  APP_EMAIL / APP_PASSWORD     compte de connexion à l'application
  YAHOO_APP_PASSWORD           mot de passe d'application Yahoo
Option : DRY_RUN=1 pour tout afficher sans rien envoyer ni enregistrer.
"""
import json, os, smtplib, ssl, sys, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

API_KEY = "AIzaSyB-MPzuy0ugXbozC8_YAMlIA52JU4GIUsg"
PROJET = "ea63-aea28"
BASE = f"https://firestore.googleapis.com/v1/projects/{PROJET}/databases/(default)/documents"
EXPEDITEUR = os.environ.get("YAHOO_EMAIL", "expertauto63@yahoo.com")
RELANCE_JOURS = int(os.environ.get("RELANCE_JOURS", "4"))
DRY = os.environ.get("DRY_RUN", "") in ("1", "true", "oui")
TEST = os.environ.get("TEST_EMAIL", "").strip()
MAX_PAR_JOUR = int(os.environ.get("MAX_PAR_JOUR", "25"))
NB = " "

# ---------------------------------------------------------------- Firestore
def http(method, url, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url.split('?')[0]} -> {e.code} {e.read().decode()[:400]}")

def connexion():
    r = http("POST", "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + API_KEY,
             {"email": os.environ["APP_EMAIL"].strip().rstrip(".").lower(), "password": os.environ["APP_PASSWORD"].strip(),
              "returnSecureToken": True})
    return r["idToken"]

def dec(v):
    if "mapValue" in v:
        return {k: dec(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [dec(x) for x in v["arrayValue"].get("values", [])]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "nullValue" in v:
        return None
    for k in ("stringValue", "booleanValue", "timestampValue", "referenceValue"):
        if k in v:
            return v[k]
    return None

def enc(x):
    if x is None:
        return {"nullValue": None}
    if isinstance(x, bool):
        return {"booleanValue": x}
    if isinstance(x, int):
        return {"integerValue": str(x)}
    if isinstance(x, float):
        return {"doubleValue": x}
    if isinstance(x, dict):
        return {"mapValue": {"fields": {k: enc(v) for k, v in x.items()}}}
    if isinstance(x, list):
        return {"arrayValue": {"values": [enc(v) for v in x]}}
    return {"stringValue": str(x)}

def lister(coll, token):
    out, page = {}, None
    while True:
        url = f"{BASE}/{coll}?pageSize=300" + (f"&pageToken={urllib.parse.quote(page)}" if page else "")
        r = http("GET", url, token=token)
        for d in r.get("documents", []):
            out[d["name"].rsplit("/", 1)[1]] = {k: dec(v) for k, v in d.get("fields", {}).items()}
        page = r.get("nextPageToken")
        if not page:
            return out

def ecrire(coll, doc_id, data, token):
    masque = "&".join("updateMask.fieldPaths=" + urllib.parse.quote(k) for k in data)
    http("PATCH", f"{BASE}/{coll}/{urllib.parse.quote(doc_id)}?{masque}",
         {"fields": {k: enc(v) for k, v in data.items()}}, token=token)

# ---------------------------------------------------------------- Calculs (identiques à l'appli)
def num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0

def r2(x):
    return round(x + 1e-9, 2)

def ttc_of(d):
    S = d.get("S") or {}
    lignes = S.get("lignes") or []
    if d.get("archive") and not lignes:
        return num(d.get("ttc_archive"))
    ht = r2(sum(r2(num(l.get("qte")) * num(l.get("pu"))) for l in lignes))
    return r2(ht + r2(ht * 0.2))

def paye_of(d):
    p = d.get("paiements") or []
    return r2(sum(num(x.get("montant")) for x in p))

def eur(x):
    s = f"{r2(x):,.2f}".replace(",", NB).replace(".", ",")
    return s + NB + "€"

def fr(iso):
    if not iso:
        return ""
    y, m, j = iso[:10].split("-")
    return f"{j}/{m}/{y}"

def titre(s):
    return " ".join(w[:1].upper() + w[1:].lower() for w in (s or "").split())

def date_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.fromisoformat(str(s)[:10] + "T12:00:00")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def date_envoi(d):
    """Date à laquelle le dossier est parti à l'assurance."""
    if isinstance(d.get("envoi"), dict) and d["envoi"].get("at"):
        return date_iso(d["envoi"]["at"])
    for h in reversed(d.get("historique") or []):
        if "Envoy" in (h.get("txt") or ""):
            return date_iso(h.get("at"))
    return date_iso(d.get("updatedAt")) or date_iso((d.get("S") or {}).get("date"))

# ---------------------------------------------------------------- Mail
def texte_relance(d, n, attendu, envoi):
    S = d.get("S") or {}
    civ, nom = S.get("client_civ") or "", S.get("client_nom") or ""
    e_ = "e" if civ == "Mme" else ""
    veh = titre(f"{S.get('marque','')} {S.get('modele','')}".strip())
    refs = [x for x in [
        S.get("n_sinistre") and "Sinistre n° " + S["n_sinistre"],
        S.get("n_contrat") and "Contrat n° " + S["n_contrat"],
        S.get("n_client") and "N° client / sociétaire : " + S["n_client"],
        S.get("exp_num") and "Rapport d'expertise n° " + S["exp_num"],
        S.get("numero") and f"Facture n° {S['numero']} du {fr(S.get('date'))}",
    ] if x]
    rang = "Relance" if n == 1 else f"{n}e relance"
    objet = " – ".join(x for x in [
        rang + " – Règlement en attente", f"{civ} {nom}".strip(),
        S.get("n_sinistre") and "Sinistre " + S["n_sinistre"],
        S.get("immat"), S.get("numero") and "Facture n° " + str(S["numero"])] if x)
    deja = f"le {envoi.strftime('%d/%m/%Y')}" if envoi else "précédemment"
    corps = f"""Madame, Monsieur,

Nous vous avons adressé {deja} le dossier de votre assuré{e_} {civ} {nom} concernant le véhicule {veh} immatriculé {S.get('immat','')}, accompagné de la cession de créance signée.

{chr(10).join(refs)}

À ce jour, nous n'avons pas reçu le règlement de {eur(attendu)}.
{"Il s'agit de notre " + rang.lower() + " pour ce dossier. " if n > 1 else ""}Nous vous remercions de bien vouloir procéder au virement dans les meilleurs délais, ou de nous indiquer si une pièce manque au dossier.

Au titre de la cession de créance, le règlement est à effectuer directement à notre société :
EXPERT AUTO 63 — IBAN FR76 3000 3020 3800 0202 5295 914 — BIC SOGEFRPP

Nous restons à votre disposition pour tout complément.

Cordialement,

Sofiane Tahidousti, Gérant – EXPERT AUTO 63
1 rue Pierre et Marie Curie, 63200 Riom
Tél. 06 26 52 33 62 – expertauto63@yahoo.com"""
    return objet, corps

class Boite:
    def __init__(self):
        self.s = None
    def envoyer(self, to, cc, objet, corps, copie_garage=True):
        m = EmailMessage()
        m["From"] = formataddr(("EXPERT AUTO 63", EXPEDITEUR))
        m["To"] = ", ".join(to)
        if cc:
            m["Cc"] = ", ".join(cc)
        m["Subject"] = objet
        m["Reply-To"] = EXPEDITEUR
        m["Message-ID"] = make_msgid(domain="yahoo.com")
        m.set_content(corps)
        dest = list(to) + list(cc) + ([EXPEDITEUR] if copie_garage else [])
        if DRY:
            return
        if self.s is None:
            self.s = smtplib.SMTP_SSL("smtp.mail.yahoo.com", 465, context=ssl.create_default_context(), timeout=60)
            self.s.login(EXPEDITEUR, os.environ["YAHOO_APP_PASSWORD"])
        self.s.send_message(m, from_addr=EXPEDITEUR, to_addrs=dest)
    def fermer(self):
        if self.s:
            self.s.quit()

# ---------------------------------------------------------------- Programme
def main():
    now = datetime.now(timezone.utc)
    token = connexion()
    dossiers = lister("dossiers", token)
    etats = lister("relances", token)
    boite, faites, manuelles, erreurs = Boite(), [], [], []
    if TEST:  # test d'envoi : une vraie relance d'exemple, envoyée seulement à l'adresse de test
        global DRY
        DRY = False
        d = next((x for x in dossiers.values() if x.get("statut") == "envoye"), None) or next(iter(dossiers.values()))
        objet, corps = texte_relance(d, 1, ttc_of(d) - paye_of(d), date_envoi(d))
        boite.envoyer([TEST], [], "[TEST] " + objet, "*** MAIL DE TEST – n'a pas été envoyé à l'assurance ***\n\n" + corps, copie_garage=False)
        boite.fermer()
        print("Mail de test envoyé à", TEST)
        return
    print(f"{len(dossiers)} dossiers lus – relance si {RELANCE_JOURS} jours sans réponse" + (" – ESSAI (rien n'est envoyé)" if DRY else ""))

    for did, d in sorted(dossiers.items(), key=lambda kv: str((kv[1].get("S") or {}).get("numero") or "")):
        S = d.get("S") or {}
        if d.get("statut") != "envoye":
            continue
        et = etats.get(did) or {}
        lib = f"N° {S.get('numero') or '?'} {S.get('client_nom','')} ({S.get('assureur_court') or 'assureur ?'})"
        if et.get("pause"):
            print("  pause      ", lib)
            continue
        ttc, paye = ttc_of(d), paye_of(d)
        franchise = num((S.get("rap") or {}).get("franchise"))
        attendu = r2(ttc - paye - (franchise if paye == 0 and franchise > 0 else 0))
        if attendu < 0.01:
            print("  soldé      ", lib)
            continue
        envoi = date_envoi(d)
        liste = et.get("liste") or []
        derniere = date_iso(et.get("derniere")) if liste else None
        ref = max([x for x in (envoi, derniere) if x] or [now])
        jours = (now - ref).total_seconds() / 86400
        if jours < RELANCE_JOURS - 0.1:  # petite marge : l'action ne passe pas toujours à la même minute
            prochaine = ref + timedelta(days=RELANCE_JOURS)
            print(f"  attente     {lib} – prochaine relance le {prochaine:%d/%m}")
            continue
        to = [x.strip() for x in (S.get("mail_assureur") or "").replace(";", ",").split(",") if x.strip()]
        cc = [x.strip() for x in (S.get("mail_expert") or "").replace(";", ",").split(",") if x.strip()]
        if not to and cc:
            to, cc = cc, []
        if not to:
            manuelles.append(f"{lib} – {eur(attendu)} : aucune adresse mail dans le dossier, à relancer par téléphone")
            print("  sans mail  ", lib)
            continue
        if len(faites) >= MAX_PAR_JOUR:
            print("  reporté    ", lib, "(limite du jour atteinte)")
            continue
        n = len(liste) + 1
        objet, corps = texte_relance(d, n, attendu, envoi)
        try:
            boite.envoyer(to, cc, objet, corps)
        except Exception as e:  # noqa
            erreurs.append(f"{lib} : {e}")
            print("  ERREUR     ", lib, e)
            continue
        entree = {"at": now.isoformat().replace("+00:00", "Z"), "to": ", ".join(to + cc), "n": n, "montant": attendu}
        if not DRY:
            ecrire("relances", did, {"dossier": did, "numero": str(S.get("numero") or ""), "client": S.get("client_nom") or "",
                                     "liste": liste + [entree], "derniere": entree["at"]}, token)
        faites.append(f"{lib} – {eur(attendu)} – relance n° {n} à {', '.join(to + cc)}")
        print(f"  RELANCÉ     {lib} – {eur(attendu)} – n° {n} -> {', '.join(to + cc)}")

    # Récapitulatif au garage (seulement s'il y a quelque chose à dire)
    if faites or manuelles or erreurs:
        parties = []
        if faites:
            parties.append("Relances envoyées aujourd'hui :\n- " + "\n- ".join(faites))
        if manuelles:
            parties.append("À relancer à la main :\n- " + "\n- ".join(manuelles))
        if erreurs:
            parties.append("Erreurs :\n- " + "\n- ".join(erreurs))
        corps = "\n\n".join(parties) + "\n\nPour arrêter les relances d'un dossier : passez-le en « Payé » dans l'application, ou cochez « Ne plus relancer » dans l'onglet Envoi.\n\n— Relances automatiques EA63 Dossiers"
        print("\n" + corps)
        try:
            boite.envoyer([EXPEDITEUR], [], f"Relances assurances du {now:%d/%m/%Y} – {len(faites)} envoyée(s)", corps, copie_garage=False)
        except Exception as e:  # noqa
            print("Récapitulatif non envoyé :", e)
    boite.fermer()
    if erreurs:
        sys.exit(1)

if __name__ == "__main__":
    main()
