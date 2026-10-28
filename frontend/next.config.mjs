/** @type {import('next').NextConfig} */
// PENN_API_LIVE / PENN_API_DEMO are set only in the Docker build (deploy/endpoint.Dockerfile): the browser calls
// /api/live/* and /api/demo/*, and this server forwards them to the two APIs in the container. Local dev leaves them
// unset and calls the API directly (NEXT_PUBLIC_PENN_API).
const apis = { live: process.env.PENN_API_LIVE, demo: process.env.PENN_API_DEMO };

export default {
  reactStrictMode: true,
  // A self-contained server for the image; local builds keep the default (standalone symlinks fail on Windows).
  ...(apis.live && { output: "standalone" }),
  // Ask PENN can take a model timeout plus one rewrite; the default 30 s proxy timeout would cut it off.
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    return Object.entries(apis).filter(([, url]) => url)
      .map(([name, url]) => ({ source: `/api/${name}/:path*`, destination: `${url}/:path*` }));
  },
};
