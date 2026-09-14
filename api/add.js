// POST /api/add — queue an investor the visitor asked for.
//
// The poller runs on a laptop behind NAT, so it cannot be called; the request
// has to land somewhere both sides reach. It goes to a Cloud Storage bucket
// the poller already has credentials for, written by a service account that
// can only *create* objects there — it cannot read them back, delete them, or
// touch the buckets holding the site and its caches.
//
// Deliberately dependency-free: signing a JWT and exchanging it for an access
// token is a dozen lines of node:crypto, and adding @google-cloud/storage would
// pull an install step into what is otherwise a static deploy. CommonJS on
// purpose too: an ESM import would need a package.json, and that is enough to
// make Vercel treat this as a Node project to build rather than a page to serve.
const { createSign } = require("node:crypto");

const BUCKET = "virgil-requests";
const SCOPE = "https://www.googleapis.com/auth/devstorage.read_write";

function b64url(buf) {
  return Buffer.from(buf).toString("base64")
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function accessToken() {
  const raw = process.env.GCP_SA_KEY_B64;
  if (!raw) throw new Error("GCP_SA_KEY_B64 not configured");
  const sa = JSON.parse(Buffer.from(raw, "base64").toString("utf8"));
  const now = Math.floor(Date.now() / 1000);
  const claim = {
    iss: sa.client_email, scope: SCOPE,
    aud: "https://oauth2.googleapis.com/token",
    iat: now, exp: now + 3600,
  };
  const body = `${b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }))}.` +
               `${b64url(JSON.stringify(claim))}`;
  const sig = createSign("RSA-SHA256").update(body).end()
    .sign(sa.private_key);
  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: `${body}.${b64url(sig)}`,
    }),
  });
  if (!r.ok) throw new Error(`token exchange failed: ${r.status}`);
  return (await r.json()).access_token;
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "POST only" });
  }
  let body = req.body;
  if (typeof body === "string") { try { body = JSON.parse(body); } catch { body = {}; } }
  body = body || {};

  const cik = String(body.cik || "").replace(/\D/g, "");
  if (!cik) return res.status(400).json({ error: "cik required" });

  const record = {
    cik: cik.padStart(10, "0"),
    name: String(body.name || "").slice(0, 120),
    label: String(body.label || "").slice(0, 120),
    person: String(body.person || "").slice(0, 120),
    requested: new Date().toISOString(),
    state: "pending",
  };

  try {
    const token = await accessToken();
    // One object per request, named by CIK: re-asking for the same investor
    // overwrites rather than queueing them twice, and the poller can list the
    // prefix without needing to read anything back here.
    const url = `https://storage.googleapis.com/upload/storage/v1/b/${BUCKET}/o` +
                `?uploadType=media&name=${encodeURIComponent(`pending/${record.cik}.json`)}`;
    const put = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(record),
    });
    if (!put.ok) throw new Error(`upload failed: ${put.status}`);
  } catch (e) {
    return res.status(502).json({ error: "could not queue", detail: String(e.message) });
  }
  return res.status(200).json({ status: "queued", cik: record.cik });
};
