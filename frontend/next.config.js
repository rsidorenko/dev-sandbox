/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  compress: true,
  images: {
    formats: ["image/avif", "image/webp"],
  },
};

module.exports = nextConfig;
