import type { RequestHandler } from '@sveltejs/kit';

const backend = 'http://127.0.0.1:8000';
const requestHeaders = ['accept', 'content-type', 'cookie', 'last-event-id', 'origin'];
const responseHeaders = ['cache-control', 'content-type', 'set-cookie', 'x-accel-buffering'];

const proxy: RequestHandler = async ({ request, url }) => {
  const headers = new Headers();
  for (const name of requestHeaders) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await globalThis.fetch(`${backend}${url.pathname}${url.search}`, {
      method: request.method,
      headers,
      body: request.method === 'POST' ? await request.text() : undefined,
      signal: request.signal,
      redirect: 'manual'
    });
  } catch {
    return new Response('Backend unavailable', { status: 502 });
  }

  const forwarded = new Headers();
  for (const name of responseHeaders) {
    const value = upstream.headers.get(name);
    if (value !== null) forwarded.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: forwarded });
};

export const GET = proxy;
export const POST = proxy;
