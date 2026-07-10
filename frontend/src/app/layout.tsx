import type { Metadata } from "next";
import { Inter } from "next/font/google";

import Providers from "./providers";
import "./globals.css";

// 레거시는 Inter + Pretendard CDN — 내부망 안정성을 위해 next/font 셀프호스팅으로
// Inter 만 싣고 한글은 Malgun Gothic 폴백(globals.css font stack)을 쓴다.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "ERP 관리 콘솔",
  description: "거래처·품목 마스터를 한 화면에서 관리하세요.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className={inter.variable}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
