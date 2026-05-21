export type DeviceType = "desktop" | "ipad" | "mobile";

export interface DeviceInfo {
  type: DeviceType;
  isIPad: boolean;
  isMobile: boolean;
  isDesktop: boolean;
  isTouchDevice: boolean;
}

export function defaultDeviceInfo(): DeviceInfo {
  return {
    type: "desktop",
    isIPad: false,
    isMobile: false,
    isDesktop: true,
    isTouchDevice: false,
  };
}

/**
 * Server-side device detection from User-Agent.
 * iPadOS 13+ sends a desktop-class UA, so we use a multi-signal approach.
 */
export function detectDeviceFromUA(userAgent: string | null): DeviceInfo {
  const ua = (userAgent ?? "").toLowerCase();

  const isIPadUA = ua.includes("ipad");
  const isIPhone = ua.includes("iphone");
  const isIPod = ua.includes("ipod");
  const isAndroid = ua.includes("android");

  // iPadOS 13+ masquerades as Mac but supports touch
  const isDesktopMacUA = ua.includes("macintosh") && !isIPhone && !isIPod && !isAndroid;

  // If UA has "iPad" literally (older iPads or some browsers)
  if (isIPadUA) {
    return {
      type: "ipad",
      isIPad: true,
      isMobile: false,
      isDesktop: false,
      isTouchDevice: true,
    };
  }

  // Phone
  if (isIPhone || isIPod || (isAndroid && ua.includes("mobile"))) {
    return {
      type: "mobile",
      isIPad: false,
      isMobile: true,
      isDesktop: false,
      isTouchDevice: true,
    };
  }

  // Android tablet (no "mobile" in UA but has android)
  if (isAndroid) {
    return {
      type: "ipad",
      isIPad: true,
      isMobile: false,
      isDesktop: false,
      isTouchDevice: true,
    };
  }

  // Desktop Mac — could be real Mac or iPadOS 13+ spoofing
  // We'll refine on client side with touch detection
  return {
    type: isDesktopMacUA ? "desktop" : "desktop",
    isIPad: false,
    isMobile: false,
    isDesktop: true,
    isTouchDevice: false,
  };
}

/**
 * Client-side refinement when UA is ambiguous (iPadOS 13+).
 * Call this on the client to correct false desktop detection.
 */
export function refineDeviceClient(serverInfo: DeviceInfo): DeviceInfo {
  if (typeof window === "undefined") return serverInfo;

  const maxTouchPoints = navigator.maxTouchPoints ?? 0;
  const hasCoarsePointer = window.matchMedia("(pointer: coarse)").matches;
  const isTouchCapable = maxTouchPoints > 0;

  // If server thought desktop but device supports touch with coarse pointer → iPad
  if (serverInfo.type === "desktop" && isTouchCapable && hasCoarsePointer) {
    const screenW = window.screen.width;
    const screenH = window.screen.height;
    const minDim = Math.min(screenW, screenH);

    // iPad screens are 768+ CSS px in both dimensions (iPad mini: 768, Pro: 1024)
    if (minDim >= 744) {
      return {
        type: "ipad",
        isIPad: true,
        isMobile: false,
        isDesktop: false,
        isTouchDevice: true,
      };
    }

    // Smaller touch screens → mobile
    return {
      type: "mobile",
      isIPad: false,
      isMobile: true,
      isDesktop: false,
      isTouchDevice: true,
    };
  }

  // If no touch, confirm desktop
  if (!isTouchCapable && !hasCoarsePointer) {
    return { ...serverInfo, isDesktop: true, isTouchDevice: false };
  }

  return serverInfo;
}
