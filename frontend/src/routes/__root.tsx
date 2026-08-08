import { useEffect, useRef, useState, type ReactNode } from "react";
import { QueryClientProvider, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  HeadContent,
  Link,
  Outlet,
  Scripts,
  useRouter,
  type ErrorComponentProps,
} from "@tanstack/react-router";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Toaster } from "@/components/ui/sonner";
import { planAuthIdentityTransition } from "@/lib/authUserBoundary";
import { resetBullExLoginState } from "@/lib/bullexLoginState";
import { isBenignRouteLoadError, isQueryCancellationError } from "@/lib/queryCancellation";
import { clearRejectionMemory } from "@/lib/robotPresentation";
import { resetRobotSettingsState } from "@/lib/robotSettings";
import { useAuth } from "@/lib/useAuth";
import { cn } from "@/lib/utils";
import appCss from "@/styles.css?url";

// Numero oficial de suporte via WhatsApp (DDD 81). Ver `docs/WHATSAPP_SUPORTE.md`.
const WHATSAPP_NUMBER = "558189998378";
const WHATSAPP_DISPLAY = "+55 81 8999-8378";
const WHATSAPP_LINK = `https://wa.me/${WHATSAPP_NUMBER}`;

interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "ElCapo AutoBot" },
      {
        name: "description",
        content: "Painel de controle do ElCapo AutoBot para operações automáticas.",
      },
      { name: "author", content: "ElCapo" },
      // Evita que o Tradutor do Chrome injete <font> no DOM e quebre o React (removeChild).
      { name: "google", content: "notranslate" },
      { property: "og:title", content: "ElCapo AutoBot" },
      {
        property: "og:description",
        content: "Painel de controle do ElCapo AutoBot para operações automáticas.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { name: "twitter:site", content: "@ElCapo" },
    ],
    links: [
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap",
      },
      { rel: "stylesheet", href: appCss },
      { rel: "icon", type: "image/svg+xml", href: "/favicon.svg" },
    ],
  }),
  shellComponent: RootDocument,
  component: RootComponent,
  notFoundComponent: NotFound,
  errorComponent: RootErrorComponent,
});

function RootDocument({ children }: { children: ReactNode }) {
  return (
    <html lang="pt-BR" translate="no" className="notranslate">
      <head>
        <HeadContent />
      </head>
      <body className="notranslate" translate="no">
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();

  // Rejeições vazias (undefined) escapam do error boundary do React e deixam
  // só o fundo escuro (“tela azul”) com “Uncaught undefined” no console.
  useEffect(() => {
    const onRejection = (event: PromiseRejectionEvent) => {
      if (!isBenignRouteLoadError(event.reason)) return;
      event.preventDefault();
      console.warn("[ROOT_BENIGN_UNHANDLED_REJECTION]", event.reason);
    };
    window.addEventListener("unhandledrejection", onRejection);
    return () => window.removeEventListener("unhandledrejection", onRejection);
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <AuthUserBoundary>
        <Outlet />
      </AuthUserBoundary>
      <WhatsAppButton />
      <Toaster />
    </QueryClientProvider>
  );
}

/**
 * Garante que trocas de usuário limpem o estado em memória (queries, Bullex,
 * configs do robô). O unmount completo do Outlet só ocorre em troca concreta
 * userA→userB — no login/logout o gate antigo causava removeChild no Chrome
 * (DOM alterado pelo Tradutor / extensões).
 */
function AuthUserBoundary({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { user, loading } = useAuth();
  const knownUserIdRef = useRef<string | null | undefined>(undefined);
  const [gated, setGated] = useState(false);

  useEffect(() => {
    if (loading) return;
    const userId = user?.id ?? null;
    const previousUserId = knownUserIdRef.current;
    const plan = planAuthIdentityTransition(previousUserId, userId);

    if (!plan.shouldResetState) {
      setGated(false);
      return;
    }

    knownUserIdRef.current = userId;
    console.log("[AUTH USER CHANGED]", {
      previous_user_id: previousUserId ?? null,
      user_id: userId,
      gate: plan.shouldGateRender,
      cancel_queries: plan.cancelInFlightQueries,
    });

    if (plan.shouldGateRender) {
      setGated(true);
    }

    const finishLocalReset = () => {
      try {
        resetBullExLoginState(
          previousUserId === undefined ? null : previousUserId,
        );
        resetBullExLoginState(userId);
        resetRobotSettingsState();
        clearRejectionMemory();
      } catch (error) {
        console.error("[AUTH_LOCAL_RESET_ERROR]", error);
      }
      console.log("[ROBOT STATE RESET]");
      setGated(false);
    };

    // Soft reset no primeiro bind / login / logout: não chama cancelQueries.
    // Caso contrário o ensureQueryData do beforeLoad de /admin vira
    // CancelledError e o RootErrorComponent mostra "Esta pagina nao carregou".
    if (!plan.cancelInFlightQueries) {
      finishLocalReset();
      return;
    }

    queryClient
      .cancelQueries()
      .catch((error: unknown) => {
        console.error("[AUTH_QUERY_CANCEL_ERROR]", error);
      })
      .finally(() => {
        queryClient.clear();
        finishLocalReset();
      });
  }, [loading, queryClient, user?.id]);

  if (loading || gated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="text-center">
          <div className="text-lg font-semibold text-foreground">Carregando sessao</div>
          <div className="mt-2 text-sm text-muted-foreground">
            Aguarde enquanto restauramos seu painel.
          </div>
        </div>
      </div>
    );
  }

  return children;
}

/**
 * Botao flutuante que abre um pop-up (Dialog) com o numero de WhatsApp do
 * suporte e um link direto para iniciar a conversa. Antes o botao abria o
 * `wa.me` direto numa nova aba; agora mostra o numero primeiro. Ver
 * `docs/WHATSAPP_SUPORTE.md`.
 */
function WhatsAppButton() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Falar pelo WhatsApp no numero ${WHATSAPP_DISPLAY}`}
        className="fixed bottom-4 right-4 z-[60] flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xl transition hover:scale-105 hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/80"
      >
        <WhatsAppIcon />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Fale com a gente no WhatsApp</DialogTitle>
            <DialogDescription>
              Tire suas duvidas ou peca suporte diretamente pelo WhatsApp.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center justify-center gap-2 rounded-md border border-border bg-muted/40 py-3 text-lg font-semibold text-foreground">
            <WhatsAppIcon className="h-5 w-5 text-primary" />
            {WHATSAPP_DISPLAY}
          </div>
          <a
            href={WHATSAPP_LINK}
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Abrir conversa no WhatsApp
          </a>
        </DialogContent>
      </Dialog>
    </>
  );
}

function WhatsAppIcon({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn(className, "fill-current")}>
      <path d="M16.04 3.2A12.78 12.78 0 0 0 5.16 22.68L3.2 28.8l6.3-1.86A12.8 12.8 0 1 0 16.04 3.2Zm0 2.18a10.62 10.62 0 1 1-5.42 19.76l-.38-.23-3.74 1.1 1.14-3.64-.25-.4A10.6 10.6 0 0 1 16.04 5.38Zm-5.1 4.42c-.25 0-.65.1-.99.47-.34.37-1.3 1.27-1.3 3.1s1.34 3.6 1.52 3.85c.19.25 2.63 4.02 6.38 5.64.89.38 1.59.61 2.13.78.9.28 1.71.24 2.35.15.72-.11 2.2-.9 2.51-1.77.31-.87.31-1.61.22-1.77-.09-.15-.34-.25-.71-.43-.37-.19-2.2-1.09-2.54-1.21-.34-.13-.59-.19-.84.18-.25.38-.96 1.21-1.18 1.46-.22.25-.43.28-.81.09-.37-.18-1.58-.58-3.01-1.86a11.3 11.3 0 0 1-2.08-2.59c-.22-.37-.02-.57.16-.75.17-.17.37-.44.56-.65.19-.22.25-.38.37-.62.13-.25.06-.47-.03-.65-.09-.19-.84-2.02-1.15-2.76-.3-.73-.61-.63-.84-.64h-.71Z" />
    </svg>
  );
}

function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">Pagina nao encontrada</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          A pagina que voce procura nao existe ou foi movida.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Ir para o inicio
          </Link>
        </div>
      </div>
    </div>
  );
}

function RootErrorComponent({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  const isBenign = isBenignRouteLoadError(error) || isQueryCancellationError(error);

  // CancelledError / rejeição vazia não são falha de produto — costumam vir do
  // bind da sessão. Recarrega a rota em vez de prender o usuário na tela azul.
  useEffect(() => {
    if (!isBenign) return;
    console.warn("[ROOT_ERROR_BENIGN_RECOVERY]", error ?? "nullish");
    void router.invalidate().finally(() => {
      reset();
    });
  }, [error, isBenign, reset, router]);

  if (isBenign) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="text-center">
          <div className="text-lg font-semibold text-foreground">Carregando sessao</div>
          <div className="mt-2 text-sm text-muted-foreground">
            Aguarde enquanto restauramos seu painel.
          </div>
        </div>
      </div>
    );
  }

  console.error("[ROOT_ERROR]", error);
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          Esta pagina nao carregou
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Ocorreu um problema do nosso lado. Tente atualizar a pagina ou voltar ao inicio.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              void router.invalidate().finally(() => {
                reset();
              });
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Tentar novamente
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Ir para o inicio
          </a>
        </div>
      </div>
    </div>
  );
}
