"use client";

import { useEffect } from "react";

import { DeviceProvider } from "@/components/device-provider";
import type { DeviceInfo } from "@/lib/device";

const CHUNK_ERROR_PATTERN = /ChunkLoadError|Loading chunk [\w-]+ failed|Failed to fetch dynamically imported module|error loading dynamically imported module/i;
const RELOAD_FLAG_KEY = "memoseed_chunk_reload_at";

/**
 * After a new deployment, clients still running an older bundle can request
 * chunk files that no longer exist on the server (build hashes change). The
 * page then looks normal (SSR HTML) but every interaction is dead — exactly
 * the "space/enter do nothing" symptom. Detect chunk-load failures and hard
 * reload once to fetch the fresh bundle.
 */
function useChunkErrorAutoReload() {
  useEffect(() => {
    function maybeReload(message: string) {
      if (!CHUNK_ERROR_PATTERN.test(message)) {
        return;
      }
      const lastReloadAt = Number(window.sessionStorage.getItem(RELOAD_FLAG_KEY) ?? "0");
      // Guard against reload loops: at most one forced reload per 30s.
      if (Date.now() - lastReloadAt < 30_000) {
        return;
      }
      window.sessionStorage.setItem(RELOAD_FLAG_KEY, String(Date.now()));
      window.location.reload();
    }

    function onError(event: ErrorEvent) {
      maybeReload(String(event.error?.message ?? event.message ?? ""));
    }
    function onUnhandledRejection(event: PromiseRejectionEvent) {
      maybeReload(String(event.reason?.message ?? event.reason ?? ""));
    }

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    };
  }, []);
}

export function AppShell({
  children,
  initialDevice,
}: {
  children: React.ReactNode;
  initialDevice: DeviceInfo;
}) {
  useChunkErrorAutoReload();
  return <DeviceProvider initialDevice={initialDevice}>{children}</DeviceProvider>;
}
