"use client";

import { DeviceProvider } from "@/components/device-provider";
import type { DeviceInfo } from "@/lib/device";

export function AppShell({
  children,
  initialDevice,
}: {
  children: React.ReactNode;
  initialDevice: DeviceInfo;
}) {
  return <DeviceProvider initialDevice={initialDevice}>{children}</DeviceProvider>;
}
