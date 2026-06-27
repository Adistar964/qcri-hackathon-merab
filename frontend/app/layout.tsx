import "./globals.css";
import type { Metadata } from "next";
import { Space_Grotesk } from "next/font/google";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-space-grotesk",
  display: "swap",
});

export const metadata: Metadata = {
  title: "FANAR AGENT — Agentic AI for Government · Healthcare · Education",
  description:
    "A desktop-grade agentic AI built on Fanar: drives a real browser, sees your screen, controls your computer, and speaks — specialized for Qatar's government, healthcare, and education.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={spaceGrotesk.variable}>
      <body className="min-h-screen bg-bg font-sans">
        <div className="noise" aria-hidden="true" />
        {children}
      </body>
    </html>
  );
}
