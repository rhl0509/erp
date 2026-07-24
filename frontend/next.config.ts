import type { NextConfig } from "next";

// FastAPI(:8040)로 프록시. same-origin 이라 CORS 불필요하고, 프론트는 상대경로 /api 를 그대로 쓴다.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8040";

const nextConfig: NextConfig = {
  output: "standalone", // prod: .next/standalone 의 server.js 경량 실행 (리버스 프록시 뒤)
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
      { source: "/openapi.json", destination: `${API_ORIGIN}/openapi.json` },
    ];
  },
};

export default nextConfig;
