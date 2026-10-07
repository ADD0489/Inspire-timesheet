// Email sending for change requests. The provider is chosen by the MAIL_PROVIDER variable:
//   mock   : never sends. Stores the rendered email in KV (v1:mailmock:<ref>) so it can be checked.
//   graph  : Microsoft 365 / Exchange Online via Microsoft Graph sendMail, from the company mailbox
//            in MAIL_SENDER (e.g. a free shared mailbox timesheets@). Secrets: GRAPH_TENANT_ID,
//            GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET (set with `wrangler secret put`).
//   resend : Resend API. Secret: RESEND_API_KEY. The sender domain must be verified in Resend.
// Email always comes from the company address, never from the staff member.

let graphToken = null; // { value, exp } cached per Worker isolate

async function graphAccessToken(env) {
  if (graphToken && graphToken.exp > Date.now() + 60_000) return graphToken.value;
  const body = new URLSearchParams({
    client_id: env.GRAPH_CLIENT_ID,
    client_secret: env.GRAPH_CLIENT_SECRET,
    scope: 'https://graph.microsoft.com/.default',
    grant_type: 'client_credentials',
  });
  const r = await fetch(`https://login.microsoftonline.com/${encodeURIComponent(env.GRAPH_TENANT_ID)}/oauth2/v2.0/token`, {
    method: 'POST', body,
  });
  if (!r.ok) throw new Error(`graph token ${r.status}`);
  const j = await r.json();
  graphToken = { value: j.access_token, exp: Date.now() + (j.expires_in || 3000) * 1000 };
  return graphToken.value;
}

export async function sendMail(env, msg) {
  // msg: { ref, from: {address, name}, to, subject, text, html }
  const provider = (env.MAIL_PROVIDER || 'mock').toLowerCase();
  if (provider === 'graph') {
    if (!env.GRAPH_TENANT_ID || !env.GRAPH_CLIENT_ID || !env.GRAPH_CLIENT_SECRET) throw new Error('graph secrets missing');
    const token = await graphAccessToken(env);
    const sender = env.MAIL_SENDER || msg.from.address;
    const r = await fetch(`https://graph.microsoft.com/v1.0/users/${encodeURIComponent(sender)}/sendMail`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: {
          subject: msg.subject,
          body: { contentType: 'HTML', content: msg.html },
          toRecipients: [{ emailAddress: { address: msg.to } }],
          internetMessageHeaders: [{ name: 'X-Inspire-Request-Ref', value: msg.ref }],
        },
        saveToSentItems: true,
      }),
    });
    if (r.status !== 202) throw new Error(`graph sendMail ${r.status}`);
    return { provider, id: msg.ref };
  }
  if (provider === 'resend') {
    if (!env.RESEND_API_KEY) throw new Error('resend key missing');
    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, 'Content-Type': 'application/json', 'Idempotency-Key': msg.ref },
      body: JSON.stringify({
        from: `${msg.from.name} <${env.MAIL_SENDER || msg.from.address}>`,
        to: [msg.to], subject: msg.subject, html: msg.html, text: msg.text,
      }),
    });
    if (!r.ok) throw new Error(`resend ${r.status}`);
    const j = await r.json();
    return { provider, id: j.id || msg.ref };
  }
  // mock (default): nothing leaves Cloudflare
  await env.STV.put(`v1:mailmock:${msg.ref}`, JSON.stringify({ ...msg, mocked: true }), { expirationTtl: 60 * 60 * 24 * 30 });
  console.log(`[mock mail] ${msg.ref} to=${msg.to} subject=${msg.subject}`);
  return { provider: 'mock', id: msg.ref };
}
