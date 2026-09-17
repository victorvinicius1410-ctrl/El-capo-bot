import { useEffect, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowRight,
  BadgeCheck,
  Bot,
  Brain,
  CreditCard,
  CheckCircle2,
  ChevronDown,
  Gauge,
  LineChart,
  Lock,
  Menu,
  MessageCircle,
  Play,
  Rocket,
  ShieldCheck,
  Smartphone,
  Sparkles,
  TimerReset,
  Wallet,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import { RobotAvatarVideo } from "@/components/RobotAvatarVideo";
import { useAuth } from "@/lib/useAuth";

// Numero oficial de suporte (ver `docs/WHATSAPP_SUPORTE.md`).
const WHATSAPP_LINK = "https://wa.me/5581989984096";

/**
 * Rastreamento do SparkleTracker (pixel do Meta por trás).
 *
 * Um script só, o da operação (`op.js`): ele faz sozinho o que antes eram o
 * `direct.js` (sessão/UTMs em `sparkle_dt_*` e `?sparkle_vid=` nos links de
 * saída) e o `pixel.js` (PageView, InitiateCheckout...). Não recolocar os dois
 * antigos junto: o pixel dispararia em dobro.
 *
 * Fica preso a ESTA rota de propósito: no `__root` ele carregaria também no
 * painel logado, e aí todo dia de uso do cliente entraria na conta do pixel
 * como tráfego novo.
 *
 * O `InitiateCheckout` sai por delegação de clique: o seletor inclui
 * `a[href*="pay."]`, que é exatamente o formato dos links da Cakto nos cards de
 * plano. **Se um dia o checkout mudar de domínio, o InitiateCheckout para de
 * contar em silêncio** — nada quebra na tela.
 */
const SPARKLE_BASE =
  "https://ap.sparkletracker.com/t/847631b4c2edcb9903a25830b2720b0901c257b3f9f7a5fc";

/**
 * Planos exibidos na LP.
 *
 * Os valores precisam bater com as ofertas criadas em
 * **Admin → Financeiro → Ofertas** (que são espelhadas na Cakto). O selo de
 * economia é calculado a partir do plano mensal — não escrever percentual na
 * mão para não sair errado quando o preço mudar.
 */
const PLANO_MENSAL_PRECO = 147.9;

interface Plano {
  nome: string;
  resumo: string;
  preco: number;
  meses: number;
  destaque?: boolean;
  beneficios: string[];
  /**
   * Link de checkout na Cakto. Vazio, o botão cai no cadastro (fluxo antigo).
   * O valor cobrado na Cakto tem que bater com o `preco` daqui: quem muda um
   * sem o outro coloca a página anunciando um preço e cobrando outro.
   */
  checkout?: string;
}

const PLANOS: Plano[] = [
  {
    nome: "Mensal",
    resumo: "Para testar o robô com a sua banca antes de assumir compromisso.",
    preco: PLANO_MENSAL_PRECO,
    meses: 1,
    checkout: "https://pay.cakto.com.br/39g5496",
    beneficios: [
      "Robô liberado sem limite de operações",
      "Análise e execução automáticas",
      "Stop win e stop loss configuráveis",
      "Painel no computador e no celular",
      "Suporte no WhatsApp",
    ],
  },
  {
    nome: "Trimestral",
    resumo: "O tempo mínimo para julgar um robô por resultado, e não por sorte.",
    preco: 377.9,
    meses: 3,
    destaque: true,
    checkout: "https://pay.cakto.com.br/3ekogau",
    beneficios: [
      "Tudo do plano mensal",
      "3 meses corridos sem renovar todo mês",
      "Histórico completo do trimestre no painel",
      "Prioridade no suporte",
    ],
  },
  {
    nome: "Anual",
    resumo: "O menor custo por dia para quem já decidiu parar de operar na mão.",
    preco: 1147.9,
    meses: 12,
    checkout: "https://pay.cakto.com.br/vx8y2as",
    beneficios: [
      "Tudo do plano trimestral",
      "Melhor preço por mês da tabela",
      "Um ano inteiro sem se preocupar com renovação",
      "Todas as atualizações do robô incluídas",
    ],
  },
];

const brl = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});

const brlCentavos = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  minimumFractionDigits: 2,
});

/** Economia (%) do plano em relação a pagar o mensal N vezes. */
function economiaPercentual(plano: Plano): number {
  const cheio = PLANO_MENSAL_PRECO * plano.meses;
  return Math.round((1 - plano.preco / cheio) * 100);
}

/** Custo diário do plano — âncora de preço usada nos cards. */
function custoPorDia(plano: Plano): number {
  return plano.preco / (plano.meses * 30);
}

export const Route = createFileRoute("/")({
  ssr: false,
  head: () => ({
    meta: [
      { title: "El Capo AutoBot: o robô que opera por você todos os dias" },
      {
        name: "description",
        content:
          "Você não perde dinheiro por falta de estratégia. Perde por ser humano. O El Capo lê o gráfico vela a vela e executa as entradas na sua conta da corretora, sem medo, sem pressa e sem cansaço.",
      },
      {
        property: "og:title",
        content: "El Capo AutoBot: o robô que opera por você todos os dias",
      },
      {
        property: "og:description",
        content:
          "Você não perde dinheiro por falta de estratégia. Perde por ser humano. O El Capo lê o gráfico vela a vela e executa as entradas na sua conta da corretora.",
      },
    ],
    scripts: [
      { src: `${SPARKLE_BASE}/op_4e2496840c/op.js`, async: true },
    ],
  }),
  component: LandingPage,
});

/**
 * Landing page pública do El Capo (rota `/`).
 *
 * Estrutura de conversão, na ordem: promessa → dor → mecanismo → prova de
 * funcionamento → celular → controle/risco → oferta → reversão de risco →
 * objeções → CTA final. Cada bloco é uma função `Lp*` separada para o dono
 * conseguir reordenar a página movendo uma linha em `LandingPage`.
 *
 * Em 15/09/2026 saíram dois blocos ("O que vem junto" e "Antes de assinar"):
 * repetiam o que o restante da página já dizia e faziam a leitura cansar antes
 * de chegar no preço. O que era único nos dois foi absorvido pelos vizinhos.
 *
 * Visitante anônimo vê a oferta; quem já está logado é levado direto para o
 * painel (mantém o comportamento antigo do redirect).
 */
function LandingPage() {
  const navigate = useNavigate();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (loading || !user) return;
    navigate({ to: "/dashboard", replace: true });
  }, [loading, navigate, user]);

  if (!loading && user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="text-center">
          <div className="text-lg font-semibold text-foreground">Carregando</div>
          <div className="mt-2 text-sm text-muted-foreground">Levando você para o painel.</div>
        </div>
      </div>
    );
  }

  return (
    <div className="lp-page relative min-h-screen overflow-hidden text-foreground">
      {/* Fundo da LP: camadas estáticas (luz de estúdio, colunas finas, grão e
          vinheta). Sem orbe pulsante nem scanline — o brilho animado dava cara
          de template. As classes são próprias da LP: a tela de login continua
          com o fundo dela. */}
      <div className="lp-atmosphere" aria-hidden="true">
        <div className="lp-bg-glow" />
        <div className="lp-bg-rules" />
        <div className="lp-bg-grain" />
        <div className="lp-bg-vignette" />
      </div>

      <div className="relative z-10">
        <LpHeader />
        <main>
          <LpHero />
          <LpStrip />
          <LpPain />
          <LpMechanism />
          <LpHowItWorks />
          <LpMobile />
          <LpControl />
          <LpPlans />
          <LpGuarantee />
          <LpFaq />
          <LpFinalCta />
        </main>
        <LpFooter />
      </div>

      <LpStickyCta />
    </div>
  );
}

const MENU_LINKS = [
  { href: "#como-funciona", texto: "Como funciona" },
  { href: "#criterio", texto: "O critério" },
  { href: "#controle", texto: "Controle e risco" },
  { href: "#planos", texto: "Planos" },
  { href: "#faq", texto: "Dúvidas" },
];

function LpHeader() {
  const [scrolled, setScrolled] = useState(false);
  const [menuAberto, setMenuAberto] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Esc fecha o menu: no celular ele cobre a tela inteira e sem isso o
  // visitante fica preso nele.
  useEffect(() => {
    if (!menuAberto) return;
    const onKey = (evento: KeyboardEvent) => {
      if (evento.key === "Escape") setMenuAberto(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuAberto]);

  return (
    <header className={`lp-header ${scrolled ? "lp-header-solid" : ""}`}>
      <div className="lp-container flex h-16 items-center justify-between gap-4">
        <a href="#topo" className="flex items-center gap-2.5">
          <span className="login-logo-ring flex h-9 w-9 items-center justify-center">
            <Bot className="h-5 w-5 text-primary-foreground" />
          </span>
          <span className="leading-none">
            <span className="login-brand-name block text-lg font-extrabold tracking-tight">
              ElCapo
            </span>
            <span className="block text-[0.6rem] font-semibold uppercase tracking-[0.24em] text-primary/80">
              AutoBot
            </span>
          </span>
        </a>

        <nav className="hidden items-center gap-7 text-sm text-muted-foreground lg:flex">
          {MENU_LINKS.map(({ href, texto }) => (
            <a key={href} className="lp-nav-link" href={href}>
              {texto}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-1.5 sm:gap-2">
          {/* "Entrar" agora aparece em qualquer largura: no celular o único
              acesso ao painel era um link discreto lá embaixo no hero. */}
          <Link to="/login" className="lp-btn-ghost">
            Entrar
          </Link>
          <a href="#planos" className="lp-btn-primary lp-btn-sm">
            <span className="sm:hidden">Ativar</span>
            <span className="hidden sm:inline">Ativar meu robô</span>
            <ArrowRight className="h-4 w-4" />
          </a>
          <button
            type="button"
            className="lp-nav-toggle lg:hidden"
            aria-expanded={menuAberto}
            aria-controls="lp-menu"
            aria-label={menuAberto ? "Fechar menu" : "Abrir menu"}
            onClick={() => setMenuAberto((aberto) => !aberto)}
          >
            {menuAberto ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </div>

      {menuAberto ? (
        <nav id="lp-menu" className="lp-nav-mobile lg:hidden">
          {MENU_LINKS.map(({ href, texto }) => (
            <a key={href} href={href} onClick={() => setMenuAberto(false)}>
              {texto}
            </a>
          ))}
          <Link to="/login" onClick={() => setMenuAberto(false)}>
            Entrar no painel
          </Link>
        </nav>
      ) : null}
    </header>
  );
}

function LpHero() {
  return (
    <section id="topo" className="relative overflow-hidden">
      <img
        src="/lp/hero-mesa.webp"
        alt=""
        aria-hidden="true"
        className="lp-hero-photo"
        fetchPriority="high"
      />

      <div className="lp-container relative z-10 pb-14 pt-6 sm:pt-10 lg:pb-24 lg:pt-16">
        {/* Grid por área: no celular a ordem é texto → robô → botões (o dono quer
            o El Capo entre a descrição e o CTA); no desktop o robô ocupa a
            coluna da direita nas duas linhas. Uma instância só do vídeo. */}
        <div className="lp-hero-grid">
          <div className="lp-hero-copy lp-rise">
            <span className="lp-eyebrow">
              <Sparkles className="h-3.5 w-3.5" />
              Robô de operações automáticas
            </span>

            <h1 className="lp-h1 mt-4">
              Você não perde por falta de estratégia.
              <span className="lp-h1-accent"> Perde por ser humano.</span>
            </h1>

            <p className="lp-lead mt-4">
              Medo, pressa e cansaço quebram mais contas do que setup errado. O El Capo lê o gráfico
              vela a vela e executa na <strong>sua</strong> conta da corretora.
            </p>
          </div>

          <div className="lp-hero-robot lp-rise-delayed">
            <LpRobotShowcase />
          </div>

          <div className="lp-hero-actions lp-rise">
            <a href="#planos" id="cta-hero" className="lp-btn-primary lp-btn-lg lp-hero-cta">
              Quero ativar meu robô
              <ArrowRight className="lp-btn-arrow h-5 w-5" />
            </a>

            <ul className="lp-hero-proof">
              <li>
                <ShieldCheck className="h-3.5 w-3.5" />7 dias de garantia
              </li>
              <li>
                <Zap className="h-3.5 w-3.5" />
                Sem instalar nada
              </li>
              <li>
                <TimerReset className="h-3.5 w-3.5" />
                Desliga num clique
              </li>
            </ul>

            <Link to="/login" className="lp-btn-outline lp-btn-lg lp-hero-secondary">
              Já sou cliente
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}

/**
 * Operações da vitrine do hero.
 *
 * **É SIMULAÇÃO E ESTÁ ROTULADA COMO TAL NA TELA.** Não vem do robô real e não
 * é resultado de cliente. Número de lucro sem esse selo, em página de produto
 * financeiro, vira promessa de rentabilidade — o aviso de risco do rodapé não
 * cobre isso sozinho. Se um dia virar dado real, trocar a fonte E o selo juntos.
 *
 * A sequência é fixa (não aleatória) para o dono conseguir conferir o que vai
 * ao ar: um loss a cada cinco resultados, que é o pedido original.
 */
interface SimOperacao {
  ativo: string;
  /** false = par OTC; true = par de mercado aberto (sem sufixo `-OTC`). */
  aberto: boolean;
  direcao: "CALL" | "PUT";
  valor: number;
  ganhou: boolean;
}

/** Payout médio observado no catálogo: 87 no OTC, 85 nos pares abertos. */
const SIM_PAYOUT_OTC = 0.87;
const SIM_PAYOUT_ABERTO = 0.85;

/**
 * Alterna OTC e mercado aberto a cada operação — mostrar só OTC dava a
 * impressão de que o robô não opera no aberto. Os pares abertos saem da lista
 * real da corretora (10 no catálogo); os OTC carregam o sufixo, como no painel.
 */
const SIM_OPERACOES: SimOperacao[] = [
  { ativo: "EURUSD-OTC", aberto: false, direcao: "CALL", valor: 25, ganhou: true },
  { ativo: "GBPJPY", aberto: true, direcao: "PUT", valor: 40, ganhou: true },
  { ativo: "AUDCAD-OTC", aberto: false, direcao: "CALL", valor: 25, ganhou: true },
  { ativo: "USDCAD", aberto: true, direcao: "CALL", valor: 30, ganhou: true },
  { ativo: "CHFJPY-OTC", aberto: false, direcao: "PUT", valor: 25, ganhou: false },
  { ativo: "EURUSD", aberto: true, direcao: "CALL", valor: 35, ganhou: true },
  { ativo: "GBPUSD-OTC", aberto: false, direcao: "PUT", valor: 25, ganhou: true },
  { ativo: "USDJPY", aberto: true, direcao: "CALL", valor: 50, ganhou: true },
  { ativo: "EURCAD-OTC", aberto: false, direcao: "PUT", valor: 25, ganhou: true },
  { ativo: "AUDJPY", aberto: true, direcao: "CALL", valor: 30, ganhou: false },
];

/** Duração de cada fase do ciclo: análise, entrada, resultado. */
const SIM_DURACAO_MS = [1600, 1900, 2600];

function LpRobotShowcase() {
  const [passo, setPasso] = useState(0);
  const [animado, setAnimado] = useState(true);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setAnimado(!mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (!animado) return;
    const timer = window.setTimeout(
      () => setPasso((atual) => atual + 1),
      SIM_DURACAO_MS[passo % SIM_DURACAO_MS.length],
    );
    return () => window.clearTimeout(timer);
  }, [animado, passo]);

  const fase = passo % 3;
  const operacao = SIM_OPERACOES[Math.floor(passo / 3) % SIM_OPERACOES.length];
  const payout = operacao.aberto ? SIM_PAYOUT_ABERTO : SIM_PAYOUT_OTC;
  const retorno = operacao.ganhou ? operacao.valor * payout : operacao.valor;

  return (
    <div className="lp-sim">
      <div className="lp-sim-head">
        <span className="lp-live-dot" aria-hidden="true" />
        <span className="lp-sim-title">El Capo em operação</span>
        <span className="lp-sim-tag">simulação</span>
      </div>

      <div className="lp-sim-stage">
        <RobotAvatarVideo className="lp-sim-robot" aria-label="Robô El Capo analisando o mercado" />

        {/* Balões decorativos: quem usa leitor de tela já tem a explicação no
            texto do hero, e a repetição a cada 2s seria ruído. */}
        <div className="lp-sim-notes" aria-hidden="true">
          <div key={`a-${operacao.ativo}`} className="lp-sim-note lp-sim-note-analise">
            <span className="lp-sim-note-label">
              Analisando
              <span className="lp-sim-mercado">{operacao.aberto ? "aberto" : "OTC"}</span>
            </span>
            <span className="lp-sim-note-value">{operacao.ativo}</span>
          </div>

          {fase >= 1 ? (
            <div key={`e-${passo - fase}`} className="lp-sim-note lp-sim-note-entrada">
              <span className="lp-sim-note-label">Entrada</span>
              <span className="lp-sim-note-value">
                {operacao.direcao} · M1 · {brl.format(operacao.valor)}
              </span>
            </div>
          ) : null}

          {fase === 2 ? (
            <div
              key={`r-${passo - fase}`}
              className={`lp-sim-note lp-sim-note-resultado ${
                operacao.ganhou ? "lp-sim-note-win" : "lp-sim-note-loss"
              }`}
            >
              <span className="lp-sim-note-label">{operacao.ganhou ? "Win" : "Loss"}</span>
              <span className="lp-sim-note-value">
                {operacao.ganhou ? "+" : "−"}
                {brlCentavos.format(retorno)}
              </span>
            </div>
          ) : null}
        </div>
      </div>

      <p className="lp-sim-legenda">
        Demonstração do painel. Valores ilustrativos, não são resultado de cliente.
      </p>

      <div className="lp-sim-rows">
        <div className="lp-sim-row">
          <LineChart className="h-4 w-4 text-primary" />
          <span>Varredura dos ativos a cada vela</span>
          <span className="lp-sim-row-tag">automático</span>
        </div>
        <div className="lp-sim-row">
          <ShieldCheck className="h-4 w-4 text-primary" />
          <span>Para sozinho no seu limite do dia</span>
          <span className="lp-sim-row-tag">seu comando</span>
        </div>
      </div>
    </div>
  );
}

const STRIP_ITEMS = [
  {
    icon: LineChart,
    title: "Análise contínua",
    text: "O robô acompanha o mercado vela a vela, sem cansar.",
  },
  { icon: Zap, title: "Execução automática", text: "Achou o ponto, entra sozinho na sua conta." },
  { icon: Gauge, title: "Stop win e stop loss", text: "Ele para no limite que você definiu." },
  {
    icon: Smartphone,
    title: "Painel no celular",
    text: "Acompanhe de onde estiver, sem instalar nada.",
  },
];

function LpStrip() {
  return (
    <section className="lp-container pb-4">
      <div className="lp-strip">
        {STRIP_ITEMS.map(({ icon: Icon, title, text }) => (
          <div key={title} className="lp-strip-item">
            <Icon className="h-5 w-5 text-primary" />
            <div>
              <p className="text-sm font-semibold text-foreground">{title}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{text}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

const PAIN_BEFORE = [
  "Horas coladas no gráfico esperando o setup aparecer",
  "Entrar com medo, sair cedo demais, entrar de novo com raiva",
  "Perder a entrada boa porque estava trabalhando ou dormindo",
  "Dobrar a mão para recuperar o prejuízo do dia",
];

const PAIN_AFTER = [
  "O robô assiste ao gráfico por você, o dia inteiro",
  "Regra fria: entra quando o critério bate, não quando dá vontade",
  "Opera enquanto você trabalha, dirige ou dorme",
  "Bateu o seu limite, ele para. Não existe revanche",
];

function LpPain() {
  return (
    <section className="lp-container lp-section">
      <div className="lp-section-head">
        <span className="lp-eyebrow">
          <TimerReset className="h-3.5 w-3.5" />O problema real
        </span>
        <h2 className="lp-h2 mt-5">A estratégia até funciona. Quem falha na hora H é você.</h2>
        <p className="lp-section-sub mt-4">
          O gráfico não pede licença para dar sinal, e não espera você estar descansado e de cabeça
          fria. O El Capo tira essa vigilância das suas costas.
        </p>
      </div>

      <div className="mt-12 grid items-stretch gap-5 lg:grid-cols-[0.85fr_1.15fr]">
        <figure className="lp-figure min-h-[16rem]">
          <img
            src="/lp/dor-trader.webp"
            alt="Trader exausto diante das telas de gráficos durante a madrugada"
            loading="lazy"
          />
          <figcaption className="lp-figure-caption">
            Toda conta zerada tem a mesma história: não foi a análise, foi a hora em que você estava
            cansado demais para seguir a própria regra.
          </figcaption>
        </figure>

        <div className="grid gap-5 sm:grid-cols-2">
          <div className="lp-compare lp-compare-before">
            <p className="lp-compare-title">Operando sozinho</p>
            <ul className="mt-5 space-y-3.5">
              {PAIN_BEFORE.map((item) => (
                <li key={item} className="lp-compare-item">
                  <XCircle className="lp-compare-icon lp-compare-icon-bad" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="lp-compare lp-compare-after">
            <p className="lp-compare-title">
              <Bot className="mr-2 inline h-4 w-4 text-primary" />
              Com o El Capo ligado
            </p>
            <ul className="mt-5 space-y-3.5">
              {PAIN_AFTER.map((item) => (
                <li key={item} className="lp-compare-item">
                  <CheckCircle2 className="lp-compare-icon lp-compare-icon-good" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

/**
 * Critérios que o robô confere antes de entrar.
 *
 * Descrição de mecanismo — é o que dá autoridade à página. Não escrever aqui
 * nada que o motor não faça de verdade; se a estratégia mudar, esta lista muda
 * junto.
 */
const CRITERIOS = [
  {
    title: "Lê o mercado vela a vela",
    text: "A cada vela nova o robô varre os ativos liberados e recalcula tudo do zero. Ele não carrega opinião da operação anterior.",
  },
  {
    title: "Confere suporte e resistência",
    text: "Antes de comprar, ele checa se o preço não está batendo de frente com um nível relevante. Entrada contra a estrutura do gráfico é descartada.",
  },
  {
    title: "Exige que os critérios batam juntos",
    text: "Um indicador sozinho não vale entrada. Se o conjunto não fecha, o robô não opera aquela vela. Ficar de fora também é uma decisão.",
  },
  {
    title: "Entra no início da vela, não no meio",
    text: "Sinal atrasado é sinal perdido. A ordem é enviada na abertura da vela, com o preço que justificou a análise.",
  },
];

function LpMechanism() {
  return (
    <section id="criterio" className="lp-container lp-section">
      <div className="lp-mech-shell">
        <div className="grid gap-10 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
          <div>
            <span className="lp-eyebrow">
              <Brain className="h-3.5 w-3.5" />O critério
            </span>
            <h2 className="lp-h2 mt-5">Ele não “acha” que vai subir. Ele confere.</h2>
            <p className="lp-section-sub mt-4">
              A diferença entre um robô e um palpite automatizado é o que acontece antes da ordem
              sair. Estes são os filtros que toda entrada do El Capo precisa passar:
            </p>

            <div className="mt-8">
              {CRITERIOS.map(({ title, text }, index) => (
                <div key={title} className="lp-crit">
                  <span className="lp-crit-index">{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <h3 className="text-base font-bold text-foreground">{title}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{text}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <figure className="lp-figure lp-figure-tech aspect-[4/3] lg:aspect-square">
            <img
              src="/lp/motor-ia.webp"
              alt="Representação do motor de análise do El Capo processando dados do mercado"
              loading="lazy"
            />
            <figcaption className="lp-figure-caption">
              Enquanto você lê esta frase, o motor já reavaliou os ativos abertos mais de uma vez.
            </figcaption>
          </figure>
        </div>
      </div>
    </section>
  );
}

const STEPS = [
  {
    icon: CreditCard,
    title: "Escolha seu plano",
    text: "Pagamento aprovado, o acesso ao painel é liberado na hora e você já entra com seu e-mail e senha.",
  },
  {
    icon: Gauge,
    title: "Conecte a corretora e ajuste",
    text: "Você liga a sua conta da corretora ao painel e define valor de entrada, stop win e stop loss. Em dois minutos o robô sabe até onde pode ir no seu lugar.",
  },
  {
    icon: Play,
    title: "Aperte iniciar e vá viver",
    text: "A partir daí é com ele: analisa, escolhe o momento, executa e mostra o placar do dia em tempo real. Você para quando quiser, com um clique.",
  },
];

function LpHowItWorks() {
  return (
    <section id="como-funciona" className="lp-container lp-section">
      <div className="lp-section-head">
        <span className="lp-eyebrow">
          <Play className="h-3.5 w-3.5" />
          Como funciona
        </span>
        <h2 className="lp-h2 mt-5">Do pagamento ao robô operando: menos de 10 minutos</h2>
        <p className="lp-section-sub mt-4">
          Sem instalar programa, sem VPS, sem configurar indicador. Tudo dentro do painel, pelo
          celular ou pelo computador.
        </p>
      </div>

      <div className="mt-12 grid gap-5 md:grid-cols-3">
        {STEPS.map(({ icon: Icon, title, text }, index) => (
          <article key={title} className="lp-step">
            <span className="lp-step-number">{String(index + 1).padStart(2, "0")}</span>
            <span className="lp-step-icon">
              <Icon className="h-5 w-5" />
            </span>
            <h3 className="mt-5 text-lg font-bold text-foreground">{title}</h3>
            <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{text}</p>
          </article>
        ))}
      </div>

      <div className="mt-10 text-center">
        <a href="#planos" className="lp-btn-primary lp-btn-lg">
          Começar agora
          <ArrowRight className="lp-btn-arrow h-5 w-5" />
        </a>
      </div>
    </section>
  );
}

function LpMobile() {
  return (
    <section className="lp-container lp-section">
      <div className="grid items-center gap-10 lg:grid-cols-[0.8fr_1.2fr]">
        <figure className="lp-figure mx-auto aspect-[3/4] w-full max-w-sm">
          <img
            src="/lp/painel-celular.webp"
            alt="Mão segurando um celular com o painel do El Capo aberto"
            loading="lazy"
          />
        </figure>

        <div className="text-center lg:text-left">
          <span className="lp-eyebrow">
            <Smartphone className="h-3.5 w-3.5" />
            No seu bolso
          </span>
          <h2 className="lp-h2 mt-5">Ligue de manhã e volte a viver a sua vida</h2>
          <p className="lp-section-sub mx-auto mt-4 max-w-xl lg:mx-0">
            Nada para instalar, nada para configurar toda vez. Você liga o robô antes de sair de
            casa e olha o placar quando der vontade.
          </p>

          <ul className="mx-auto mt-7 max-w-xl space-y-3 text-left">
            {[
              "Liga e desliga de qualquer lugar, sem computador",
              "Placar do dia atualizado entrada por entrada, enquanto acontece",
              "Cada operação vem com a leitura que levou àquele clique",
              "O limite de perda continua valendo mesmo com a tela fechada",
            ].map((item) => (
              <li key={item} className="lp-fit-item">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

const CONTROL_POINTS = [
  {
    icon: Wallet,
    title: "O dinheiro continua com você",
    text: "O robô opera dentro da sua própria conta da corretora. Em nenhum momento o seu saldo passa por nós.",
  },
  {
    icon: Lock,
    title: "Você define o tamanho da entrada",
    text: "Ninguém decide por você quanto vale cada operação. O valor é configurado por você e pode ser mudado quando quiser.",
  },
  {
    icon: ShieldCheck,
    title: "Limite diário respeitado",
    text: "Stop win e stop loss encerram a sessão automaticamente. O robô não insiste, não dobra aposta e não tenta recuperar prejuízo por conta própria.",
  },
  {
    icon: TimerReset,
    title: "Desligar leva um clique",
    text: "Mudou de ideia no meio do dia? Aperta parar e acabou. O controle nunca sai da sua mão.",
  },
];

function LpControl() {
  return (
    <section id="controle" className="lp-container lp-section">
      <div className="lp-control-shell">
        <div className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
          <div>
            <span className="lp-eyebrow">
              <ShieldCheck className="h-3.5 w-3.5" />
              Controle e risco
            </span>
            <h2 className="lp-h2 mt-5">Automático não quer dizer sem controle</h2>
            <p className="lp-section-sub mt-4">
              Automatizar é tirar a emoção da operação, não abrir mão do comando. Toda regra que o
              robô segue foi você quem definiu.
            </p>
            <p className="lp-risk-note mt-6">
              Operar no mercado envolve risco e pode gerar prejuízo. O El Capo automatiza a execução
              da estratégia. Nenhum robô garante lucro. Opere sempre com um valor que você pode
              arriscar.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            {CONTROL_POINTS.map(({ icon: Icon, title, text }) => (
              <div key={title} className="lp-control-card">
                <Icon className="h-5 w-5 text-primary" />
                <h3 className="mt-3.5 text-sm font-bold text-foreground">{title}</h3>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{text}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function LpPlans() {
  return (
    <section id="planos" className="lp-container lp-section">
      <div className="lp-section-head">
        <span className="lp-eyebrow">
          <CreditCard className="h-3.5 w-3.5" />
          Planos
        </span>
        <h2 className="lp-h2 mt-5">Escolha por quanto tempo o robô vai trabalhar para você</h2>
        <p className="lp-section-sub mt-4">
          É o mesmo robô, com tudo liberado, nos três planos. O que muda é o tempo de acesso:
          quanto mais tempo, menor fica o custo por dia.
        </p>
      </div>

      <div className="lp-plan-grid mt-12 grid gap-5 lg:grid-cols-3">
        {PLANOS.map((plano) => {
          const economia = economiaPercentual(plano);
          const porMes = plano.preco / plano.meses;
          const classeBotao = plano.destaque ? "lp-btn-primary" : "lp-btn-outline";

          return (
            <article
              key={plano.nome}
              className={`lp-plan ${plano.destaque ? "lp-plan-featured" : ""}`}
            >
              {plano.destaque ? <span className="lp-plan-badge">Mais escolhido</span> : null}

              <div className="lp-plan-head">
                <h3 className="text-lg font-bold text-foreground">{plano.nome}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                  {plano.resumo}
                </p>
              </div>

              <div className="lp-plan-price">
                <span className="lp-plan-amount">{brlCentavos.format(plano.preco)}</span>
                <span className="lp-plan-cycle">
                  {plano.meses === 1 ? "por mês" : `a cada ${plano.meses} meses`}
                </span>
              </div>

              <p className="lp-plan-perday">
                ≈ {brlCentavos.format(custoPorDia(plano))} por dia de operação
              </p>

              {plano.meses > 1 ? (
                <p className="lp-plan-permonth">
                  Equivale a <strong>{brlCentavos.format(porMes)}</strong> por mês
                  {economia > 0 ? (
                    <span className="lp-plan-save">economia de {economia}%</span>
                  ) : null}
                </p>
              ) : (
                <p className="lp-plan-permonth">Renovação mensal, cancele quando quiser</p>
              )}

              <ul className="lp-plan-list">
                {plano.beneficios.map((beneficio) => (
                  <li key={beneficio}>
                    <BadgeCheck className="h-4 w-4 shrink-0 text-primary" />
                    <span>{beneficio}</span>
                  </li>
                ))}
              </ul>

              {plano.checkout ? (
                <a href={plano.checkout} className={`${classeBotao} lp-btn-lg mt-auto w-full`}>
                  <Rocket className="h-4 w-4" />
                  Ativar o {plano.nome.toLowerCase()}
                </a>
              ) : (
                <Link to="/register" className={`${classeBotao} lp-btn-lg mt-auto w-full`}>
                  <Rocket className="h-4 w-4" />
                  Ativar o {plano.nome.toLowerCase()}
                </Link>
              )}
            </article>
          );
        })}
      </div>

      <div className="lp-plan-note">
        <ShieldCheck className="h-5 w-5 shrink-0 text-primary" />
        <p>
          Pagamento por cartão ou Pix em ambiente seguro. Você tem <strong>7 dias</strong> para
          pedir reembolso a partir da compra, como manda o Código de Defesa do Consumidor. Se o
          robô não for para você, é só avisar.
        </p>
      </div>
    </section>
  );
}

function LpGuarantee() {
  return (
    <section className="lp-container lp-section">
      <div className="lp-guarantee">
        <div className="lp-guarantee-seal" aria-hidden="true">
          <strong>7</strong>
          <span>dias</span>
        </div>

        <div>
          <h2 className="lp-h2">O risco de testar é nosso</h2>
          <p className="lp-section-sub mt-4">
            Assine, conecte a corretora e veja o robô operar de verdade. Se em{" "}
            <strong>7 dias</strong> você concluir que não é para você, pede o reembolso e devolvemos
            o valor. Sem questionário e sem precisar justificar.
          </p>

          <div className="mt-7 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
            <a href="#planos" className="lp-btn-primary lp-btn-lg">
              Testar sem risco por 7 dias
              <ArrowRight className="lp-btn-arrow h-5 w-5" />
            </a>
            <a
              href={WHATSAPP_LINK}
              target="_blank"
              rel="noreferrer noopener"
              className="lp-btn-outline lp-btn-lg"
            >
              <MessageCircle className="h-5 w-5" />
              Tirar uma dúvida antes
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}

const FAQ = [
  {
    q: "Preciso saber analisar gráfico?",
    a: "Não. A análise é do robô. Você configura o valor de entrada e os seus limites do dia, e ele cuida do resto.",
  },
  {
    q: "Quanto eu vou ganhar por mês?",
    a: "Não existe resposta honesta para essa pergunta, e quem te der um número está vendendo ilusão. O resultado depende do mercado, da sua banca e dos limites que você configurar. Existem dias negativos. O que o robô entrega é execução disciplinada, não rentabilidade garantida.",
  },
  {
    q: "Onde as operações acontecem?",
    a: "Na sua própria conta da corretora, que você conecta ao painel. O saldo é seu e fica lá. O robô apenas executa as entradas, e em nenhum momento o dinheiro passa por nós.",
  },
  {
    q: "Quanto preciso ter na corretora para operar?",
    a: "O plano dá acesso ao robô; a banca com que ele vai operar é sua e fica na corretora. Comece com um valor de entrada pequeno, veja o robô rodando por alguns dias e aumente só quando estiver confortável.",
  },
  {
    q: "Preciso instalar alguma coisa ou contratar VPS?",
    a: "Não. O robô roda nos nossos servidores e o painel abre no navegador do computador ou do celular. Você pode desligar o seu aparelho que ele continua operando.",
  },
  {
    q: "E se eu me arrepender?",
    a: "Você tem 7 dias a partir da compra para pedir reembolso, como manda o Código de Defesa do Consumidor. Depois disso, pode cancelar a renovação quando quiser: o acesso segue ativo até o fim do período já pago.",
  },
];

function LpFaq() {
  return (
    <section id="faq" className="lp-container lp-section">
      <div className="lp-section-head">
        <span className="lp-eyebrow">
          <MessageCircle className="h-3.5 w-3.5" />
          Dúvidas frequentes
        </span>
        <h2 className="lp-h2 mt-5">O que todo mundo pergunta antes de ligar o robô</h2>
      </div>

      <div className="mx-auto mt-10 max-w-3xl space-y-3">
        {FAQ.map(({ q, a }) => (
          <details key={q} className="lp-faq">
            <summary className="lp-faq-summary">
              <span>{q}</span>
              <ChevronDown className="lp-faq-chevron h-4 w-4" />
            </summary>
            <p className="lp-faq-answer">{a}</p>
          </details>
        ))}
      </div>
    </section>
  );
}

function LpFinalCta() {
  return (
    <section className="lp-container lp-section">
      <div className="lp-final">
        <span className="lp-eyebrow">
          <Bot className="h-3.5 w-3.5" />
          Comece hoje
        </span>
        <h2 className="lp-h2 mt-5 text-balance">
          Enquanto você decide, o mercado já fechou mais uma vela.
        </h2>
        <p className="lp-section-sub mx-auto mt-4 max-w-2xl">
          Amanhã o gráfico vai dar sinal de novo, com ou sem você na frente da tela. A única
          pergunta é se vai ter alguém disciplinado o suficiente para executar.
        </p>

        <div className="mt-9 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-center">
          <a href="#planos" className="lp-btn-primary lp-btn-lg">
            Escolher meu plano
            <ArrowRight className="lp-btn-arrow h-5 w-5" />
          </a>
          <a
            href={WHATSAPP_LINK}
            target="_blank"
            rel="noreferrer noopener"
            className="lp-btn-outline lp-btn-lg"
          >
            <MessageCircle className="h-5 w-5" />
            Falar com a equipe
          </a>
        </div>

        <p className="mt-6 text-xs text-muted-foreground">
          7 dias para pedir reembolso · Já tem conta?{" "}
          <Link to="/login" className="lp-inline-link">
            Entrar no painel
          </Link>
        </p>
      </div>
    </section>
  );
}

/**
 * Barra fixa de CTA — some no desktop, onde o header já cumpre esse papel.
 *
 * Só aparece depois que o botão do hero sai da tela: na primeira dobra ela era
 * CTA repetido E cobria o rodapé do próprio botão que deveria estar visível.
 */
function LpStickyCta() {
  const [visivel, setVisivel] = useState(false);

  useEffect(() => {
    const alvo = document.getElementById("cta-hero");
    if (!alvo || typeof IntersectionObserver === "undefined") {
      setVisivel(true);
      return;
    }
    const observer = new IntersectionObserver(([entrada]) => setVisivel(!entrada.isIntersecting), {
      threshold: 0,
    });
    observer.observe(alvo);
    return () => observer.disconnect();
  }, []);

  if (!visivel) return null;

  return (
    <div className="lp-sticky-cta">
      <p className="lp-sticky-cta-label">
        <strong>A partir de {brlCentavos.format(custoPorDia(PLANOS[2]))} por dia</strong>7 dias para
        pedir reembolso
      </p>
      <a href="#planos" className="lp-btn-primary lp-btn-sm shrink-0">
        Ver planos
        <ArrowRight className="h-4 w-4" />
      </a>
    </div>
  );
}

function LpFooter() {
  return (
    <footer className="lp-footer">
      <div className="lp-container py-10">
        <div className="flex flex-col items-center justify-between gap-6 sm:flex-row">
          <div className="flex items-center gap-2.5">
            <span className="login-logo-ring flex h-8 w-8 items-center justify-center">
              <Bot className="h-4 w-4 text-primary-foreground" />
            </span>
            <span className="text-sm font-bold text-foreground">ElCapo AutoBot</span>
          </div>
          <div className="flex items-center gap-6 text-sm text-muted-foreground">
            <Link to="/login" className="lp-nav-link">
              Entrar
            </Link>
            <Link to="/register" className="lp-nav-link">
              Criar conta
            </Link>
            <a
              className="lp-nav-link"
              href={WHATSAPP_LINK}
              target="_blank"
              rel="noreferrer noopener"
            >
              Suporte
            </a>
          </div>
        </div>

        <p className="mt-8 text-center text-xs leading-relaxed text-muted-foreground sm:text-left">
          Aviso de risco: operações no mercado financeiro envolvem risco de perda do capital
          investido. O El Capo AutoBot é uma ferramenta de automação de execução e não constitui
          recomendação de investimento nem promessa de rentabilidade. Resultados passados não
          garantem resultados futuros. Opere apenas com valores que você pode arriscar.
        </p>
        <p className="mt-4 text-center text-xs text-muted-foreground/70 sm:text-left">
          © {new Date().getFullYear()} ElCapo AutoBot. Todos os direitos reservados.
        </p>
      </div>
    </footer>
  );
}
