"use client";

import { createContext, useContext, useEffect, useState } from "react";
import type { DeviceInfo } from "@/lib/device";
import { defaultDeviceInfo, refineDeviceClient } from "@/lib/device";

const DeviceContext = createContext<DeviceInfo>(defaultDeviceInfo());

export function useDevice(): DeviceInfo {
  return useContext(DeviceContext);
}

export function DeviceProvider({
  children,
  initialDevice,
}: {
  children: React.ReactNode;
  initialDevice: DeviceInfo;
}) {
  const [device, setDevice] = useState<DeviceInfo>(initialDevice);

  useEffect(() => {
    // Refine on client: iPadOS 13+ spoofs Mac UA, needs touch + screen check
    const refined = refineDeviceClient(initialDevice);
    if (refined.type !== device.type) {
      setDevice(refined);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <DeviceContext.Provider value={device}>{children}</DeviceContext.Provider>
  );
}
