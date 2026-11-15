/** @type {import('next').NextConfig} */
const backend = (process.env.FINN_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const allowedDevOrigins = (process.env.FINN_ALLOWED_DEV_ORIGINS || '')
  .split(',')
  .map((origin) => origin.trim())
  .filter(Boolean);

const nextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ['127.0.0.1', '192.168.*.*', '10.*.*.*', '172.16.*.*', ...allowedDevOrigins],
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
