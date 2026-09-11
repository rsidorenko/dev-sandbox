/** @type {import('next').NextConfig} */
const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").origin;
const upgradeInsecureRequests =
  process.env.NODE_ENV === "production" ? "; upgrade-insecure-requests" : "";
const developmentScriptPolicy =
  process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "";

const nextConfig = {
  output: "standalone",
  compress: true,
  poweredByHeader: false,
  images: {
    formats: ["image/avif", "image/webp"],
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value:
              // mc.yandex.ru убран из CSP вместе с полным выключением Метрики
              // (PD-аудит 2026-09-11): ноль внешних передач с сайта.
              `default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; object-src 'none'; script-src 'self' 'unsafe-inline'${developmentScriptPolicy}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' ${apiOrigin}${upgradeInsecureRequests}`,
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
