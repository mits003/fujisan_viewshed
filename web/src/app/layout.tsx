import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ご当地富士可視域マップ",
  description: "Local Fuji mountains viewshed map",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja" className="h-full">
      <body className="h-full m-0">{children}</body>
    </html>
  );
}
