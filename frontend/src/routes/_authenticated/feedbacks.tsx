import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CircleDollarSign,
  ExternalLink,
  Loader2,
  MessageSquareQuote,
  Send,
  Star,
  Video,
} from "lucide-react";
import {
  ApiError,
  createFeedback,
  listMyFeedbacks,
  listPublicFeedbacks,
  type FeedbackItem,
  type FeedbackStatus,
} from "@/lib/api";
import { formatBrasiliaDate } from "@/lib/brasiliaTime";
import { useAuth } from "@/lib/useAuth";

export const Route = createFileRoute("/_authenticated/feedbacks")({
  head: () => ({ meta: [{ title: "Feedbacks — ElCapo AutoBot" }] }),
  component: FeedbacksPage,
});

function FeedbacksPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const defaultName = useMemo(() => {
    const email = user?.email ?? "";
    const local = email.split("@")[0] || "Operador";
    return local.replace(/[._-]+/g, " ").trim() || "Operador";
  }, [user?.email]);

  const [authorName, setAuthorName] = useState("");
  const [description, setDescription] = useState("");
  const [result, setResult] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [rating, setRating] = useState<number | null>(5);

  useEffect(() => {
    setAuthorName((current) => (current.trim() ? current : defaultName));
  }, [defaultName]);

  const publicFeedbacks = useInfiniteQuery({
    queryKey: ["feedbacks", "public"],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = await listPublicFeedbacks(pageParam);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_offset : undefined),
    staleTime: 15000,
  });

  const myFeedbacks = useInfiniteQuery({
    queryKey: ["feedbacks", "mine"],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = await listMyFeedbacks(pageParam);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_offset : undefined),
    staleTime: 10000,
  });

  const submitMutation = useMutation({
    mutationFn: async () => {
      const response = await createFeedback({
        author_name: authorName.trim(),
        description: description.trim(),
        result: result.trim() || null,
        video_url: videoUrl.trim() || null,
        rating,
      });
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    onSuccess: () => {
      setDescription("");
      setResult("");
      setVideoUrl("");
      setRating(5);
      void queryClient.invalidateQueries({ queryKey: ["feedbacks"] });
    },
  });

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submitMutation.mutate();
  }

  const approved = publicFeedbacks.data?.pages.flatMap((page) => page.items) ?? [];
  const mine = myFeedbacks.data?.pages.flatMap((page) => page.items) ?? [];
  const isLoading = publicFeedbacks.isLoading || myFeedbacks.isLoading;
  const error = publicFeedbacks.error ?? myFeedbacks.error;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="page-title">Feedbacks</h1>
        <p className="page-lead">
          Compartilhe vídeo, resultado e descrição com a comunidade. Os envios ficam visíveis após
          aprovação do administrador.
        </p>
      </header>

      <section className="page-surface p-5 sm:p-6">
        <div className="mb-4 flex items-center gap-2">
          <MessageSquareQuote className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold tracking-wide text-foreground/90">
            Enviar feedback
          </h2>
        </div>

        <form onSubmit={handleSubmit} className="grid gap-4">
          <label className="grid gap-1.5 text-sm">
            <span className="text-muted-foreground">Nome exibido</span>
            <input
              value={authorName}
              onChange={(event) => setAuthorName(event.target.value)}
              className="rounded-lg border border-border bg-background/60 px-3 py-2.5 outline-none ring-primary/40 focus:ring-2"
              maxLength={80}
              required
            />
          </label>

          <label className="grid gap-1.5 text-sm">
            <span className="text-muted-foreground">Descrição</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className="min-h-28 resize-y rounded-lg border border-border bg-background/60 px-3 py-2.5 outline-none ring-primary/40 focus:ring-2"
              placeholder="Conte o que aconteceu na operação, o contexto e o que aprendeu..."
              maxLength={2000}
              required
            />
          </label>

          <label className="grid gap-1.5 text-sm">
            <span className="text-muted-foreground">Resultado (opcional)</span>
            <input
              value={result}
              onChange={(event) => setResult(event.target.value)}
              className="rounded-lg border border-border bg-background/60 px-3 py-2.5 outline-none ring-primary/40 focus:ring-2"
              placeholder="Ex.: WIN +R$ 180,00 ou LOSS −R$ 40,00"
              maxLength={120}
            />
          </label>

          <label className="grid gap-1.5 text-sm">
            <span className="text-muted-foreground">Vídeo (opcional)</span>
            <input
              type="url"
              value={videoUrl}
              onChange={(event) => setVideoUrl(event.target.value)}
              className="rounded-lg border border-border bg-background/60 px-3 py-2.5 outline-none ring-primary/40 focus:ring-2"
              placeholder="Cole o link do YouTube, Vimeo ou .mp4/.webm"
              maxLength={500}
            />
            <span className="text-xs text-muted-foreground">
              Aceita links https do YouTube, Vimeo ou arquivo de vídeo direto.
            </span>
          </label>

          <div className="grid gap-1.5 text-sm">
            <span className="text-muted-foreground">Nota (opcional)</span>
            <div className="flex flex-wrap items-center gap-1">
              {[1, 2, 3, 4, 5].map((value) => {
                const active = rating != null && value <= rating;
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setRating(value)}
                    className="rounded-md p-1.5 transition hover:bg-accent"
                    aria-label={`${value} estrelas`}
                    aria-pressed={rating === value}
                  >
                    <Star
                      className={`h-5 w-5 ${
                        active ? "fill-primary text-primary" : "text-muted-foreground"
                      }`}
                    />
                  </button>
                );
              })}
              {rating != null ? (
                <button
                  type="button"
                  onClick={() => setRating(null)}
                  className="ml-2 text-xs text-muted-foreground underline-offset-2 hover:underline"
                >
                  Limpar nota
                </button>
              ) : null}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={submitMutation.isPending}
              className="page-cta inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              {submitMutation.isPending ? "Enviando..." : "Enviar para aprovação"}
            </button>
            {submitMutation.isSuccess ? (
              <p className="text-sm text-primary">
                Feedback enviado. Ele aparece na lista pública após aprovação.
              </p>
            ) : null}
            {submitMutation.error ? (
              <p className="text-sm text-destructive">
                {submitMutation.error instanceof Error
                  ? submitMutation.error.message
                  : "Falha ao enviar feedback."}
              </p>
            ) : null}
          </div>
        </form>
      </section>

      {error ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive-foreground">
          <strong>Não foi possível carregar os feedbacks.</strong>{" "}
          {error instanceof Error ? error.message : "Erro na API."}
        </div>
      ) : null}

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold tracking-wide text-foreground/90">
            Feedbacks aprovados
          </h2>
          {isLoading ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : null}
        </div>

        {approved.length === 0 && !publicFeedbacks.isLoading ? (
          <div className="page-surface p-5 text-sm text-muted-foreground">
            Ainda não há feedbacks aprovados. Seja o primeiro a enviar.
          </div>
        ) : (
          <div className="grid gap-3">
            {approved.map((item) => (
              <FeedbackCard key={item.id} item={item} />
            ))}
            {publicFeedbacks.hasNextPage ? (
              <LoadMoreButton
                loading={publicFeedbacks.isFetchingNextPage}
                onClick={() => void publicFeedbacks.fetchNextPage()}
              />
            ) : null}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold tracking-wide text-foreground/90">Meus envios</h2>
        {mine.length === 0 && !myFeedbacks.isLoading ? (
          <div className="page-surface p-5 text-sm text-muted-foreground">
            Você ainda não enviou feedbacks.
          </div>
        ) : (
          <div className="grid gap-3">
            {mine.map((item) => (
              <FeedbackCard key={item.id} item={item} showStatus />
            ))}
            {myFeedbacks.hasNextPage ? (
              <LoadMoreButton
                loading={myFeedbacks.isFetchingNextPage}
                onClick={() => void myFeedbacks.fetchNextPage()}
              />
            ) : null}
          </div>
        )}
      </section>
    </div>
  );
}

function FeedbackCard({ item, showStatus = false }: { item: FeedbackItem; showStatus?: boolean }) {
  const description = item.description || item.content;
  const embed = item.video_url ? resolveVideoEmbed(item.video_url) : null;

  return (
    <article className="page-surface space-y-3 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-semibold text-foreground">{item.author_name}</div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {showStatus ? <StatusBadge status={item.status} /> : null}
          <time dateTime={item.created_at}>{formatFeedbackDate(item.created_at)}</time>
        </div>
      </div>

      {item.rating != null ? (
        <div className="flex items-center gap-0.5" aria-label={`${item.rating} de 5`}>
          {[1, 2, 3, 4, 5].map((value) => (
            <Star
              key={value}
              className={`h-3.5 w-3.5 ${
                value <= item.rating! ? "fill-primary text-primary" : "text-muted-foreground/40"
              }`}
            />
          ))}
        </div>
      ) : null}

      {item.result ? (
        <div className="inline-flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-sm font-semibold text-primary">
          <CircleDollarSign className="h-4 w-4 shrink-0" />
          <span>Resultado: {item.result}</span>
        </div>
      ) : null}

      {embed ? (
        <div className="overflow-hidden rounded-xl border border-border bg-black/40">
          {embed.kind === "iframe" ? (
            <div className="relative aspect-video w-full">
              <iframe
                src={embed.src}
                title={`Vídeo de ${item.author_name}`}
                className="absolute inset-0 h-full w-full"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
              />
            </div>
          ) : embed.kind === "video" ? (
            <video
              src={embed.src}
              controls
              className="aspect-video max-h-[420px] w-full bg-black"
              preload="metadata"
            >
              Seu navegador não suporta vídeo embutido.
            </video>
          ) : (
            <a
              href={embed.src}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 px-4 py-3 text-sm text-primary hover:underline"
            >
              <Video className="h-4 w-4" />
              Abrir vídeo
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
        </div>
      ) : null}

      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Descrição
        </div>
        <p className="text-sm leading-relaxed text-foreground/90">{description}</p>
      </div>
    </article>
  );
}

function StatusBadge({ status }: { status: FeedbackStatus }) {
  const labels: Record<FeedbackStatus, string> = {
    pending: "Aguardando aprovação",
    approved: "Aprovado",
    rejected: "Rejeitado",
    archived: "Arquivado",
  };
  const tones: Record<FeedbackStatus, string> = {
    pending: "border-amber-500/40 bg-amber-500/10 text-amber-100",
    approved: "border-primary/40 bg-primary/10 text-primary",
    rejected: "border-destructive/40 bg-destructive/10 text-destructive-foreground",
    archived: "border-border bg-muted text-muted-foreground",
  };
  return (
    <span className={`rounded-md border px-2 py-0.5 font-medium ${tones[status]}`}>
      {labels[status]}
    </span>
  );
}

function LoadMoreButton({ loading, onClick }: { loading: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={loading}
      onClick={onClick}
      className="mx-auto inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-semibold transition hover:bg-accent disabled:opacity-50"
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
      {loading ? "Carregando..." : "Ver mais 10"}
    </button>
  );
}

type VideoEmbed =
  | { kind: "iframe"; src: string }
  | { kind: "video"; src: string }
  | { kind: "link"; src: string };

function resolveVideoEmbed(rawUrl: string): VideoEmbed | null {
  try {
    const url = new URL(rawUrl);
    if (!["http:", "https:"].includes(url.protocol)) return null;

    const host = url.hostname.replace(/^www\./, "").toLowerCase();

    if (host === "youtu.be") {
      const id = url.pathname.split("/").filter(Boolean)[0];
      if (id) return { kind: "iframe", src: `https://www.youtube.com/embed/${id}` };
    }

    if (host === "youtube.com" || host === "m.youtube.com" || host === "youtube-nocookie.com") {
      const short = url.pathname.match(/^\/shorts\/([^/]+)/)?.[1];
      const embed = url.pathname.match(/^\/embed\/([^/]+)/)?.[1];
      const watch = url.searchParams.get("v");
      const id = short || embed || watch;
      if (id) return { kind: "iframe", src: `https://www.youtube.com/embed/${id}` };
    }

    if (host === "vimeo.com") {
      const id = url.pathname.split("/").filter(Boolean)[0];
      if (id && /^\d+$/.test(id)) {
        return { kind: "iframe", src: `https://player.vimeo.com/video/${id}` };
      }
    }

    if (/\.(mp4|webm|ogg)(\?.*)?$/i.test(url.pathname)) {
      return { kind: "video", src: rawUrl };
    }

    return { kind: "link", src: rawUrl };
  } catch {
    return null;
  }
}

function formatFeedbackDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return formatBrasiliaDate(date, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}
