import "./globals.css";
import type { Metadata } from "next";
import dynamic from "next/dynamic";
import { Inter } from "next/font/google";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-inter",
  display: "swap",
});

// WebGL mesh-gradient background — client-only (needs WebGL, no SSR).
const MeshBackground = dynamic(() => import("@/components/ui/MeshBackground"), { ssr: false });

export const metadata: Metadata = {
  title: "MERAB AGENT — Agentic AI for Government · Healthcare · Education",
  description:
    "A desktop-grade agentic AI built on Fanar: drives a real browser, sees your screen, controls your computer, and speaks — specialized for Qatar's government, healthcare, and education.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-black font-sans">
        {/* Animated MERAB shader background */}
        <div className="fixed inset-0 -z-10">
          <MeshBackground />
        </div>
        <div className="grid-overlay" aria-hidden="true" />
        <div className="noise" aria-hidden="true" />
        <main className="relative z-10 min-h-screen">{children}</main>
      </body>
    </html>
  );
}
