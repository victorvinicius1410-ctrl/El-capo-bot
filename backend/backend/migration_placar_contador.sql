-- Placar como contador único no banco (pedido do dono, 01/10/2026).
--
-- Hoje o mesmo placar vive em 5 cópias (memória do runtime, memória do
-- gateway, snapshot Redis, robot_states e o espelho robot_trades) e quase
-- todo defeito de placar foi uma cópia discordando da outra. Aqui o placar é
-- UMA linha por cliente, alterada só por funções atômicas:
--
--   placar_lancar    soma o efeito de uma ordem — no máximo uma vez por ordem
--                    dentro do período (chave única), mesmo com resultado
--                    repetido ou atrasado.
--   placar_apagar    Shift+O: desfaz exatamente o que aquela ordem somou.
--   placar_reiniciar "Reiniciar placar": zera e abre um período novo.
--   placar_vitrine   Shift+O "gerar placar": ajusta só a vitrine (sintético,
--                    nunca entra no stop).
--   placar_semear    carga inicial (só se o cliente ainda não tem placar).
--
-- As regras de negócio (gale conta um ciclo, LOSS oculto do modo LIVE, empate
-- não conta, lucro do ciclo com gale) continuam no robô: ele manda os DELTAS
-- que já aplicaria ao placar; o banco só garante soma única e atômica.
--
-- Rodar no SQL Editor do Supabase (produção e staging). Idempotente.
-- Conferir depois: select * from public.placar limit 1;
-- Ver backend/docs/PLACAR_CONTADOR.md.

create table if not exists public.placar (
    user_id        text primary key,
    desde          timestamptz not null default now(),
    wins           integer not null default 0,
    losses         integer not null default 0,
    profit         numeric(14, 2) not null default 0,
    -- Stop: só ordem real (a vitrine do Shift+O não para ninguém).
    stop_wins      integer not null default 0,
    stop_losses    integer not null default 0,
    stop_ganho     numeric(14, 2) not null default 0,
    stop_perda     numeric(14, 2) not null default 0,
    versao         bigint not null default 0,
    atualizado_em  timestamptz not null default now()
);

create table if not exists public.placar_lancamentos (
    user_id     text not null,
    desde       timestamptz not null,
    order_id    text not null,
    d_wins      integer not null default 0,
    d_losses    integer not null default 0,
    d_profit    numeric(14, 2) not null default 0,
    -- Dinheiro real da ordem (perna de gale inclusa) para o stop em R$.
    dinheiro    numeric(14, 2) not null default 0,
    sintetica   boolean not null default false,
    criado_em   timestamptz not null default now(),
    primary key (user_id, desde, order_id)
);

alter table public.placar enable row level security;
alter table public.placar_lancamentos enable row level security;
-- Sem policies: só a service role (backend) lê e escreve.

create or replace function public._placar_aplicar(
    p_user text, p_wins integer, p_losses integer, p_profit numeric,
    p_dinheiro numeric, p_sintetica boolean, p_sinal integer
) returns void language sql as $$
    update public.placar set
        wins        = wins + p_sinal * p_wins,
        losses      = losses + p_sinal * p_losses,
        profit      = profit + p_sinal * p_profit,
        stop_wins   = stop_wins + case when p_sintetica then 0 else p_sinal * p_wins end,
        stop_losses = stop_losses + case when p_sintetica then 0 else p_sinal * p_losses end,
        stop_ganho  = stop_ganho + case when not p_sintetica and p_dinheiro > 0 then p_sinal * p_dinheiro else 0 end,
        stop_perda  = stop_perda + case when not p_sintetica and p_dinheiro < 0 then p_sinal * abs(p_dinheiro) else 0 end,
        versao      = versao + 1,
        atualizado_em = now()
    where user_id = p_user;
$$;

create or replace function public.placar_lancar(
    p_user text, p_order text, p_wins integer, p_losses integer,
    p_profit numeric, p_dinheiro numeric, p_sintetica boolean default false
) returns setof public.placar language plpgsql as $$
declare
    v_desde timestamptz;
    v_novo integer;
begin
    insert into public.placar (user_id) values (p_user) on conflict do nothing;
    -- Trava a linha: lançamentos simultâneos do mesmo cliente entram em fila.
    select desde into v_desde from public.placar where user_id = p_user for update;
    insert into public.placar_lancamentos
        (user_id, desde, order_id, d_wins, d_losses, d_profit, dinheiro, sintetica)
    values (p_user, v_desde, p_order, p_wins, p_losses, p_profit, p_dinheiro, p_sintetica)
    on conflict do nothing;
    get diagnostics v_novo = row_count;
    if v_novo = 1 then
        perform public._placar_aplicar(p_user, p_wins, p_losses, p_profit, p_dinheiro, p_sintetica, 1);
    end if;
    return query select * from public.placar where user_id = p_user;
end;
$$;

-- Apaga a ordem e o fechamento de ciclo dela (`<ordem>#ciclo`, usado pelo
-- gale abandonado: a perna entra primeiro só com o dinheiro, o LOSS do ciclo
-- depois).
create or replace function public.placar_apagar(p_user text, p_order text)
returns setof public.placar language plpgsql as $$
declare
    v_desde timestamptz;
    v record;
begin
    select desde into v_desde from public.placar where user_id = p_user for update;
    for v in
        delete from public.placar_lancamentos
         where user_id = p_user and desde = v_desde
           and (order_id = p_order or order_id = p_order || '#ciclo')
        returning *
    loop
        perform public._placar_aplicar(p_user, v.d_wins, v.d_losses, v.d_profit, v.dinheiro, v.sintetica, -1);
    end loop;
    return query select * from public.placar where user_id = p_user;
end;
$$;

create or replace function public.placar_reiniciar(p_user text, p_desde timestamptz default now())
returns setof public.placar language plpgsql as $$
begin
    insert into public.placar (user_id, desde) values (p_user, p_desde)
    on conflict (user_id) do update set
        desde = excluded.desde, wins = 0, losses = 0, profit = 0,
        stop_wins = 0, stop_losses = 0, stop_ganho = 0, stop_perda = 0,
        versao = public.placar.versao + 1, atualizado_em = now();
    return query select * from public.placar where user_id = p_user;
end;
$$;

create or replace function public.placar_vitrine(
    p_user text, p_wins integer, p_losses integer, p_profit numeric
) returns setof public.placar language plpgsql as $$
declare
    v public.placar%rowtype;
begin
    insert into public.placar (user_id) values (p_user) on conflict do nothing;
    select * into v from public.placar where user_id = p_user for update;
    if v.wins <> p_wins or v.losses <> p_losses or v.profit <> p_profit then
        insert into public.placar_lancamentos
            (user_id, desde, order_id, d_wins, d_losses, d_profit, dinheiro, sintetica)
        values (p_user, v.desde, 'vitrine:' || gen_random_uuid()::text,
                p_wins - v.wins, p_losses - v.losses, p_profit - v.profit, 0, true);
        perform public._placar_aplicar(p_user, p_wins - v.wins, p_losses - v.losses,
                                       p_profit - v.profit, 0, true, 1);
    end if;
    return query select * from public.placar where user_id = p_user;
end;
$$;

create or replace function public.placar_semear(
    p_user text, p_desde timestamptz, p_wins integer, p_losses integer, p_profit numeric,
    p_stop_wins integer, p_stop_losses integer, p_stop_ganho numeric, p_stop_perda numeric
) returns setof public.placar language plpgsql as $$
begin
    insert into public.placar
        (user_id, desde, wins, losses, profit, stop_wins, stop_losses, stop_ganho, stop_perda, versao)
    values (p_user, p_desde, p_wins, p_losses, p_profit, p_stop_wins, p_stop_losses,
            p_stop_ganho, p_stop_perda, 1)
    on conflict (user_id) do nothing;
    return query select * from public.placar where user_id = p_user;
end;
$$;
