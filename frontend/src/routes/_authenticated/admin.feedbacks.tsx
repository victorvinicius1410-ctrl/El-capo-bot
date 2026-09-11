import { createFileRoute } from "@tanstack/react-router";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, Check, Loader2, MessageSquareQuote, Trash2, X } from "lucide-react";
import { useState } from "react";
import {
  ApiError,
  adminApproveFeedback,
  adminArchiveFeedback,
  adminDeleteFeedback,
  adminListFeedbacks,
  adminRejectFeedback,
  type FeedbackItem,
  type FeedbackStatus,
} from "@/lib/api";
import { formatBrasiliaDate } from "@/lib/brasiliaTime";

export const Route = createFileRoute("/_authenticated/admin/feedbacks")({
  head: () => ({ meta: [{ title: "Feedbacks — Administração ElCapo" }] }),
  component: AdminFeedbacksPage,
});

const FILTERS: readonly { id: FeedbackStatus; label: string }[] = [
  { id: "pending", label: "Solicitações" },
  { id: "rejected", label: "Rejeitados" },
  { id: "approved", label: "Aprovados" },
  { id: "archived", label: "Arquivados" },
];

function AdminFeedbacksPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<FeedbackStatus>("pending");
  const feedbacks = useInfiniteQuery({
    queryKey: ["admin", "feedbacks", filter],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = await adminListFeedbacks(filter, pageParam);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
      return response.data;
    },
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.next_offset : undefined),
    staleTime: 30_000,
  });
  const mutation = useMutation({
    mutationFn: async ({
      action,
      id,
    }: {
      action: "approve" | "reject" | "archive" | "delete";
      id: string;
    }) => {
      const response =
        action === "approve"
          ? await adminApproveFeedback(id)
          : action === "reject"
            ? await adminRejectFeedback(id)
            : action === "archive"
              ? await adminArchiveFeedback(id)
              : await adminDeleteFeedback(id);
      if (!response.ok) throw new ApiError(response.error, response.code, response.status);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "feedbacks"] });
      void queryClient.invalidateQueries({ queryKey: ["feedbacks"] });
    },
  });
  const items = feedbacks.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="page-title flex items-center gap-2">
          <MessageSquareQuote className="h-6 w-6 text-primary" />
          Feedbacks
        </h1>
        <p className="page-lead">Modere solicitações e gerencie depoimentos publicados.</p>
      </header>
      <nav className="flex flex-wrap gap-2 rounded-2xl border border-border bg-card p-2">
        {FILTERS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setFilter(item.id)}
            className={`rounded-lg px-4 py-2 text-sm font-semibold ${
              filter === item.id ? "bg-primary text-primary-foreground" : "hover:bg-accent"
            }`}
          >
            {item.label}
          </button>
        ))}
      </nav>
      {feedbacks.error || mutation.error ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          {(feedbacks.error ?? mutation.error) instanceof Error
            ? (feedbacks.error ?? (mutation.error as Error)).message
            : "Falha ao moderar feedbacks."}
        </div>
      ) : null}
      {feedbacks.isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-primary" />
      ) : items.length === 0 ? (
        <div className="page-surface p-8 text-center text-sm text-muted-foreground">
          Nenhum feedback nesta categoria.
        </div>
      ) : (
        <div className="grid gap-4">
          {items.map((item) => (
            <FeedbackCard
              key={item.id}
              item={item}
              busy={mutation.isPending && mutation.variables?.id === item.id}
              onAction={(action) => {
                if (
                  action !== "delete" ||
                  window.confirm("Excluir este feedback definitivamente?")
                ) {
                  mutation.mutate({ action, id: item.id });
                }
              }}
            />
          ))}
        </div>
      )}
      {feedbacks.hasNextPage ? (
        <button
          type="button"
          onClick={() => void feedbacks.fetchNextPage()}
          disabled={feedbacks.isFetchingNextPage}
          className="mx-auto flex rounded-lg border px-4 py-2 text-sm font-semibold"
        >
          {feedbacks.isFetchingNextPage ? "Carregando..." : "Ver mais 10"}
        </button>
      ) : null}
    </div>
  );
}

function FeedbackCard({
  item,
  busy,
  onAction,
}: {
  item: FeedbackItem;
  busy: boolean;
  onAction: (action: "approve" | "reject" | "archive" | "delete") => void;
}) {
  return (
    <article className="page-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">{item.author_name}</h2>
          <p className="text-xs text-muted-foreground">
            Enviado em {formatDate(item.created_at)} · {item.user_id}
          </p>
        </div>
        <span className="rounded-md bg-primary/10 px-2 py-1 text-xs font-semibold text-primary">
          {statusLabel(item.status)}
        </span>
      </div>
      {item.result ? (
        <p className="mt-3 text-sm font-semibold text-primary">Resultado: {item.result}</p>
      ) : null}
      <p className="mt-3 text-sm leading-relaxed">{item.description || item.content}</p>
      {item.status === "rejected" && item.rejection_expires_at ? (
        <p className="mt-3 text-xs text-destructive">
          Exclusão automática em {formatDate(item.rejection_expires_at)}
        </p>
      ) : null}
      <div className="mt-4 flex flex-wrap gap-2">
        {item.status === "pending" ? (
          <>
            <Action
              Icon={Check}
              label="Aprovar"
              disabled={busy}
              onClick={() => onAction("approve")}
            />
            <Action Icon={X} label="Rejeitar" disabled={busy} onClick={() => onAction("reject")} />
          </>
        ) : null}
        {item.status === "rejected" ? (
          <Action
            Icon={Check}
            label="Reavaliar e aprovar"
            disabled={busy}
            onClick={() => onAction("approve")}
          />
        ) : null}
        {item.status === "approved" ? (
          <Action
            Icon={Archive}
            label="Arquivar"
            disabled={busy}
            onClick={() => onAction("archive")}
          />
        ) : null}
        {item.status === "approved" || item.status === "archived" ? (
          <Action
            Icon={Trash2}
            label="Excluir"
            disabled={busy}
            onClick={() => onAction("delete")}
            destructive
          />
        ) : null}
      </div>
    </article>
  );
}

function Action({
  Icon,
  label,
  disabled,
  onClick,
  destructive = false,
}: {
  Icon: React.ComponentType<{ className?: string }>;
  label: string;
  disabled: boolean;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-semibold disabled:opacity-50 ${
        destructive ? "border-destructive/40 text-destructive" : "border-border hover:bg-accent"
      }`}
    >
      <Icon className="h-4 w-4" />
      {label}
    </button>
  );
}

function statusLabel(status: FeedbackStatus) {
  return {
    pending: "Solicitação",
    rejected: "Rejeitado",
    approved: "Aprovado",
    archived: "Arquivado",
  }[status];
}

function formatDate(value: string) {
  return formatBrasiliaDate(value) || value;
}
