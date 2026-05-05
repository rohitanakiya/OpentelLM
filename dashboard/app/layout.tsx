import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenTelLM",
  description: "Open-source LLM observability",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
