#!/usr/bin/env python3
"""Met à jour un champ d'un dossier de l'appli (lancé à la main depuis GitHub).
Entrées : NUMERO (n° de facture), CHAMP (ex. mail_assureur, n_sinistre), VALEUR."""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))
import relancer as R

AUTORISES = {"mail_assureur", "mail_expert", "n_sinistre", "n_contrat", "n_client", "client_tel", "client_mail", "immat", "vin"}
num, champ, val = os.environ["NUMERO"].strip(), os.environ["CHAMP"].strip(), os.environ["VALEUR"].strip()
if champ not in AUTORISES:
    sys.exit(f"Champ refusé : {champ} (autorisés : {', '.join(sorted(AUTORISES))})")
tok = R.connexion()
D = R.lister("dossiers", tok)
cibles = [(i, d) for i, d in D.items() if str((d.get("S") or {}).get("numero") or "") == num]
if len(cibles) != 1:
    sys.exit(f"{len(cibles)} dossier(s) trouvé(s) avec le n° {num}")
did, d = cibles[0]
avant = (d.get("S") or {}).get(champ, "")
hist = (d.get("historique") or []) + [{"at": R.datetime.now(R.timezone.utc).isoformat().replace("+00:00", "Z"), "by": "Claude", "txt": f"{champ} : {val}"}]
R.http("PATCH", f"{R.BASE}/dossiers/{did}?updateMask.fieldPaths=S.{champ}&updateMask.fieldPaths=historique",
       {"fields": {"S": {"mapValue": {"fields": {champ: {"stringValue": val}}}}, "historique": R.enc(hist)}}, token=tok)
print(f"Dossier n° {num} ({(d.get('S') or {}).get('client_nom','')}) : {champ} « {avant} » → « {val} »")
