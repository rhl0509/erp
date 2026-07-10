import type { NextConfig } from "next";

// FastAPI(:8000)로 프록시. same-origin 이라 CORS 불필요하고, 프론트는 상대경로 /api 를 그대로 쓴다.
// /legacy 는 스트랭글러 공존 기간 동안 레거시 index.html(FastAPI '/')로 연결(같은 origin → localStorage 토큰 공유).
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone", // prod: .next/standalone 의 server.js 경량 실행 (리버스 프록시 뒤)
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
      { source: "/openapi.json", destination: `${API_ORIGIN}/openapi.json` },
      { source: "/legacy", destination: `${API_ORIGIN}/` },
      { source: "/static/:path*", destination: `${API_ORIGIN}/static/:path*` },
    ];
  },
};

export default nextConfig;
