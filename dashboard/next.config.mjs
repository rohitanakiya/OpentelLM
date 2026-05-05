// The WebSocket server runs as a separate Node process on port 3001.
// We rewrite /api/ws/* to ws-server so the browser sees one origin.
const config = {
  async rewrites() {
    const wsHost = process.env.WS_INTERNAL_URL ?? 'http://localhost:3001';
    return [
      { source: '/api/ws/:path*', destination: wsHost + '/:path*' },
    ];
  },
  output: 'standalone',
  reactStrictMode: true,
};
export default config;
