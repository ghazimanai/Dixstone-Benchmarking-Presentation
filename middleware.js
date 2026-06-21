export const config = {
  matcher: ['/((?!_vercel).*)'],
};

const PASSWORD = 'Dixstone2026*';
const COOKIE = 'dx_auth';
const TOKEN = 'dx-ok-2026-7f3ac9';

function loginPage(error) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dixstone &middot; Confidential</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0f141d;color:#f2f4f8;font-family:'Inter',system-ui,Arial,sans-serif;padding:1rem;}
  .card{width:100%;max-width:380px;background:#161d28;border:1px solid #26303d;border-radius:14px;padding:2.2rem 2rem;text-align:center;}
  .eyebrow{font-size:0.74rem;letter-spacing:0.22em;text-transform:uppercase;color:#E8A020;font-weight:700;}
  h1{font-family:Georgia,'Times New Roman',serif;font-weight:700;font-size:1.45rem;margin:0.6rem 0 0.3rem;line-height:1.2;}
  p{color:#8a93a3;font-size:0.9rem;margin:0 0 1.3rem;line-height:1.45;}
  input{width:100%;padding:0.8rem 0.9rem;border-radius:8px;border:1px solid #26303d;background:#0f141d;color:#f2f4f8;font-size:1rem;margin-bottom:0.9rem;}
  input:focus{outline:none;border-color:#E8A020;}
  button{width:100%;padding:0.8rem;border:0;border-radius:8px;background:#E8A020;color:#0f141d;font-weight:700;font-size:0.95rem;cursor:pointer;letter-spacing:0.03em;}
  .err{color:#ff6b6b;font-size:0.85rem;margin-bottom:0.7rem;min-height:1.1em;}
</style></head><body>
  <form class="card" method="post" autocomplete="off">
    <div class="eyebrow">Dixstone &middot; Confidential</div>
    <h1>Rigs Peer Benchmarking Review</h1>
    <p>This presentation is confidential. Please enter the password to continue.</p>
    <div class="err">${error ? 'Incorrect password &mdash; please try again.' : ''}</div>
    <input type="password" name="password" placeholder="Password" autofocus aria-label="Password" />
    <button type="submit">View presentation</button>
  </form>
</body></html>`;
  return new Response(html, {
    status: error ? 401 : 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

export default async function middleware(request) {
  const cookie = request.headers.get('cookie') || '';
  const authed = cookie.split(';').some((c) => c.trim() === `${COOKIE}=${TOKEN}`);
  if (authed) return; // already unlocked

  if (request.method === 'POST') {
    let pw = '';
    try {
      const form = await request.formData();
      pw = (form.get('password') || '').toString();
    } catch (e) {}
    if (pw === PASSWORD) {
      const url = new URL(request.url);
      return new Response(null, {
        status: 303,
        headers: {
          Location: url.pathname + url.search,
          'Set-Cookie': `${COOKIE}=${TOKEN}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=86400`,
        },
      });
    }
    return loginPage(true);
  }
  return loginPage(false);
}
