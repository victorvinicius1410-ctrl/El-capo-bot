import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

/**
 * Contratos estáticos das otimizações de navegação admin.
 * Falham se alguém reintroduzir getMyAccess frio no beforeLoad ou polling no /admin.
 */
describe("admin nav performance contracts", () => {
  it("beforeLoad admin usa ensureMeAccessData (cache + retry de CancelledError)", () => {
    const source = readFileSync(join(here, "../routes/_authenticated/admin.tsx"), "utf8");
    assert.match(source, /ensureMeAccessData\(context\.queryClient\)/);
    assert.doesNotMatch(source, /await getMyAccess\(\)/);
    const meAccess = readFileSync(join(here, "./meAccessQuery.ts"), "utf8");
    assert.match(meAccess, /ensureQueryData\(meAccessQueryOptions\(\)\)/);
    assert.match(meAccess, /isQueryCancellationError/);
    const cancellation = readFileSync(join(here, "./queryCancellation.ts"), "utf8");
    assert.match(cancellation, /export function isQueryCancellationError/);
  });

  it("AppShell desliga robô em rotas /admin", () => {
    const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
    assert.match(shell, /pathname\.startsWith\("\/admin"\)/);
    assert.match(shell, /!isAdminRoute/);
    assert.match(shell, /meAccessQueryOptions\(\)/);
  });

  it("QueryClient tem staleTime default e preload delay", () => {
    const router = readFileSync(join(here, "../router.tsx"), "utf8");
    assert.match(router, /staleTime:\s*30_000/);
    assert.match(router, /refetchOnWindowFocus:\s*false/);
    assert.match(router, /defaultPreloadDelay:\s*80/);
  });

  it("apiRequest usa cache de identidade de sessão", () => {
    const api = readFileSync(join(here, "./api.ts"), "utf8");
    assert.match(api, /readSessionIdentityCache/);
    assert.match(api, /writeSessionIdentityCache/);
    assert.match(api, /clearSessionIdentityCache/);
  });

  it("Acessos prefetcha Pedidos no hover e usa query canônica", () => {
    const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
    assert.match(shell, /prefetchAdminClientsSegment/);
    const page = readFileSync(
      join(here, "../routes/_authenticated/admin.clientes.tsx"),
      "utf8",
    );
    assert.match(page, /prefetchAdminClientsSegment\(context\.queryClient/);
    assert.match(page, /placeholderData:\s*keepPreviousData/);
    assert.match(page, /ADMIN_PENDING_STALE_TIME_MS/);
    const query = readFileSync(join(here, "./adminClientsQuery.ts"), "utf8");
    assert.match(query, /ADMIN_PENDING_STALE_TIME_MS\s*=\s*45_000/);
  });
});
