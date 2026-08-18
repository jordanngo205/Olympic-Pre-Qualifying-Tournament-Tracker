/**
 * Cloudflare Worker — "Update now" button backend.
 *
 * The dashboard is static files on GitHub Pages, so it cannot trigger a
 * rebuild itself: firing a GitHub workflow needs a token, and anything in the
 * page is readable by anyone (GitHub also auto-revokes tokens it finds in
 * public repos, so that approach breaks itself). This Worker holds the
 * password and the token instead, and is the only thing that talks to GitHub.
 *
 * Deploy (one time, free tier):
 *   1. dash.cloudflare.com → Workers & Pages → Create → Worker → paste this
 *   2. Settings → Variables → add two secrets:
 *        DASHBOARD_PASSWORD   the word staff will type
 *        GITHUB_TOKEN         a fine-grained PAT with Actions: read+write
 *                             on this repo only
 *   3. Settings → Variables → add two plain variables:
 *        GITHUB_REPO          jordanngo205/Olympic-Pre-Qualifying-Tournament-Tracker
 *        ALLOWED_ORIGIN       https://jordanngo205.github.io
 *   4. Copy the Worker URL and rebuild the dashboard with:
 *        python3 fiba_scrape.py --event ... --refresh-endpoint <worker url>
 */

const WORKFLOW = 'update-dashboard.yml';

export default {
  async fetch(request, env) {
    const origin = env.ALLOWED_ORIGIN || '*';
    const cors = {
      'Access-Control-Allow-Origin': origin,
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (request.method === 'OPTIONS') return new Response(null, { headers: cors });

    const reply = (status, body) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { ...cors, 'Content-Type': 'application/json' },
      });

    if (request.method !== 'POST') return reply(405, { error: 'POST only' });

    let password = '';
    try {
      ({ password = '' } = await request.json());
    } catch {
      return reply(400, { error: 'Bad request' });
    }

    // Compare in constant time so the password cannot be guessed a character
    // at a time by measuring how long the reply takes.
    const expected = env.DASHBOARD_PASSWORD || '';
    const a = new TextEncoder().encode(password);
    const b = new TextEncoder().encode(expected);
    let same = a.length === b.length;
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      if (a[i] !== b[i]) same = false;
    }
    if (!expected || !same) return reply(401, { error: 'Wrong password' });

    const res = await fetch(
      `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: 'application/vnd.github+json',
          'User-Agent': 'fiba-dashboard-refresh',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ref: 'main' }),
      },
    );

    // GitHub answers 204 with an empty body when the run has been queued.
    if (res.status === 204) return reply(200, { ok: true });
    return reply(502, { error: `GitHub returned ${res.status}`, detail: await res.text() });
  },
};
