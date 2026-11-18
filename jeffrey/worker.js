// Jeffrey – relais entre l'appli EA63 Dossiers et Claude (Anthropic).
// À coller dans un Worker Cloudflare. Secret à ajouter dans le Worker : ANTHROPIC_API_KEY
// La clé reste ici, jamais dans l'appli. Seuls les comptes connectés à l'appli peuvent poser des questions.

const FIREBASE_API_KEY = "AIzaSyB-MPzuy0ugXbozC8_YAMlIA52JU4GIUsg";
const ORIGINES = ["https://kevin97-oos.github.io"];
const MODELE = "claude-haiku-5-5";

const SYSTEME = `Tu es Jeffrey, l'assistant du garage EXPERT AUTO 63 (carrosserie et pare-brise, Riom).
Tu réponds en français, à l'oral : phrases courtes et simples, pas de tableaux, pas de markdown, pas d'astérisques.
Pour les questions sur le garage, tu t'appuies sur les données des dossiers fournies (JSON) et sur la date du jour ; si l'information n'y est pas, dis-le simplement, n'invente jamais un dossier ou un montant.
Tu peux aussi répondre à toutes les autres questions du gérant (voitures, réparations, assurances, démarches, calculs, conseils, culture générale…), comme un assistant normal.
Montants en euros avec une virgule (ex. 974,45 €). Dates au format jour/mois.
Statuts : devis, cours = en cours, verif = à vérifier, envoye = envoyé à l'assurance, paye = payé.
"reste" = ce qui n'est pas encore encaissé. "relances" = nombre de mails de relance automatiques envoyés à l'assurance (tous les 4 jours tant que ce n'est pas payé).
Tu peux aussi aider à rédiger un court message ou mail pour une assurance si on te le demande.
Quand tu parles d'un dossier, cite son numéro et le nom du client.`;

function cors(origin) {
  const o = ORIGINES.includes(origin) ? origin : ORIGINES[0];
  return { "Access-Control-Allow-Origin": o, "Access-Control-Allow-Methods": "POST, OPTIONS",
           "Access-Control-Allow-Headers": "Content-Type", "Vary": "Origin" };
}
const json = (o, status, h) => new Response(JSON.stringify(o), { status, headers: { ...h, "Content-Type": "application/json" } });

export default {
  async fetch(req, env) {
    const h = cors(req.headers.get("Origin") || "");
    if (req.method === "OPTIONS") return new Response(null, { headers: h });
    if (req.method !== "POST") return json({ ok: true, info: "Jeffrey est en ligne" }, 200, h);
    let b;
    try { b = await req.json(); } catch { return json({ erreur: "requête illisible" }, 400, h); }

    // 1. Vérifier que la personne est connectée à l'appli
    const v = await fetch("https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=" + FIREBASE_API_KEY, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idToken: b.idToken || "" }) });
    if (!v.ok) return json({ erreur: "non connecté" }, 401, h);

    // 2. Demander à Claude
    const histo = (Array.isArray(b.historique) ? b.historique : []).slice(-8)
      .filter(m => m && (m.role === "user" || m.role === "assistant") && typeof m.texte === "string")
      .map(m => ({ role: m.role, content: m.texte.slice(0, 2000) }));
    const messages = [...histo, { role: "user", content:
      `Date du jour : ${b.aujourdhui || ""}\nDossiers (JSON) :\n${JSON.stringify(b.dossiers || []).slice(0, 150000)}\n\nQuestion : ${String(b.question || "").slice(0, 1000)}` }];
    // l'API veut une conversation qui commence par l'utilisateur
    while (messages.length && messages[0].role !== "user") messages.shift();

    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model: MODELE, max_tokens: 1200, system: SYSTEME, messages }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) return json({ erreur: (d.error && d.error.message) || ("Claude a répondu " + r.status) }, 502, h);
    const texte = (d.content || []).filter(c => c.type === "text").map(c => c.text).join("\n").trim();
    return json({ reponse: texte }, 200, h);
  }
};
