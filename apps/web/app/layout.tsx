import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "auto-research",
  description: "Autonomous AI research agent — live monitor",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-ink-700 bg-ink-900/80 backdrop-blur sticky top-0 z-20">
          <div className="max-w-[1600px] mx-auto px-6 py-3 flex items-center justify-between">
            <Link href="/" className="flex items-center gap-2 font-mono">
              <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
              <span className="font-semibold">auto-research</span>
              <span className="text-ink-400 text-xs">/ local v1</span>
            </Link>
            <nav className="flex items-center gap-4 text-sm">
              <Link href="/" className="text-ink-300 hover:text-ink-100">Runs</Link>
              <Link
                href="/runs/new"
                className="bg-accent text-ink-900 px-3 py-1.5 rounded font-medium hover:bg-accent/80"
              >
                + New Run
              </Link>
            </nav>
          </div>
        </header>
        <main className="max-w-[1600px] mx-auto px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
