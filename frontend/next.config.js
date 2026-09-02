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
              `default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; object-src 'none'; script-src 'self' 'unsafe-inline'${developmentScriptPolicy} https://mc.yandex.ru; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://mc.yandex.ru; font-src 'self'; connect-src 'self' ${apiOrigin} https://mc.yandex.ru wss://mc.yandex.ru${upgradeInsecureRequests}`,
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
