import type { Metadata } from "next";
import { headers } from "next/headers";
import { detectDeviceFromUA, defaultDeviceInfo } from "@/lib/device";
import { AppShell } from "@/components/app-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "MemoSeed",
  description: "English memory operating system for long-term learning.",
  appleWebApp: {
    capable: true,
    title: "MemoSeed",
    statusBarStyle: "default",
  },
  other: {
    "apple-mobile-web-app-capable": "yes",
    "apple-mobile-web-app-status-bar-style": "default",
  },
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  let deviceInfo = defaultDeviceInfo();
  try {
    const headersList = await headers();
    const ua = headersList.get("user-agent");
    deviceInfo = detectDeviceFromUA(ua);
  } catch {
    // Use default (desktop) if headers can't be read
  }

  return (
    <html lang="zh-CN">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-title" content="MemoSeed" />
        <meta name="apple-mobile-web-app-status-bar-style" content="default" />
        <meta name="format-detection" content="telephone=no" />
      </head>
      <body>
        <AppShell initialDevice={deviceInfo}>{children}</AppShell>
      </body>
    </html>
  );
}
