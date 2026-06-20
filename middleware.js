export const config = {
  matcher: ['/((?!_vercel).*)'],
};

const PASSWORD = 'Dixstone2026*';

export default function middleware(request) {
  const auth = request.headers.get('authorization') || '';
  if (auth.startsWith('Basic ')) {
    try {
      const decoded = atob(auth.slice(6)); // "username:password"
      const password = decoded.slice(decoded.indexOf(':') + 1);
      if (password === PASSWORD) {
        return; // correct password — let the request through
      }
    } catch (e) {
      // fall through to 401
    }
  }
  return new Response('Confidential — password required.', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Dixstone Benchmarking — confidential. Enter password (leave username blank).", charset="UTF-8"',
      'Content-Type': 'text/plain',
    },
  });
}
