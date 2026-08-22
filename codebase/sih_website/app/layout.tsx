import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VeriSwarm Rescue | Search wider. Verify before acting.",
  description:
    "An offline-first, evidence-backed multi-drone search and rescue system for SIH 26177.",
  icons: {
    icon: "/veriswarm-drone.png",
    shortcut: "/veriswarm-drone.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
