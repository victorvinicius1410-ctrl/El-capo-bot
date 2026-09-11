import { useEffect, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowRight,
  BadgeCheck,
  BarChart3,
  Bot,
  CalendarClock,
  CreditCard,
  CheckCircle2,
  ChevronDown,
  Gauge,
  LineChart,
  Lock,
  MessageCircle,
  Play,
  Rocket,
  ShieldCheck,
  Smartphone,
  Sparkles,
  TimerReset,
  Wallet,
  XCircle,
  Zap,
} from "lucide-react";
import { RobotAvatarVideo } from "@/components/RobotAvatarVideo";
import { useAuth } from "@/lib/useAuth";

// Numero oficial de suporte (ver `docs/WHATSAPP_SUPORTE.md`).
const WHATSAPP_LINK = "https://wa.me/558189998378";

/**
 * Planos exibidos na LP.
 *
 * Os valores precisam bater com as ofertas criadas em
 * **Admin → Financeiro → Ofertas** (que são espelhadas na Cakto). O selo de
 * economia é calculado a partir do plano mensal — não escrever percentual na
 * mão para não sair errado quando o preço mudar.
 */
const PLANO_MENSAL_PRECO = 197;

interface Plano {
  nome: string;
  resumo: string;
  preco: number;
  meses: number;
  destaque?: boolean;
  beneficios: string[];
}

const PLANOS: Plano[] = [
  {
    nome: "Mensal",
    resumo: "Coloque o robô para operar já no próximo pregão.",
    preco: PLANO_MENSAL_PRECO,
    meses: 1,
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
    resumo: "Tempo suficiente para o robô mostrar consistência.",
    preco: 497,
    meses: 3,
    destaque: true,
    beneficios: [
      "Tudo do plano mensal",
      "3 meses garantidos sem renovar todo mês",
      "Histórico completo do trimestre no painel",
      "Prioridade no suporte",
    ],
  },
  {
    nome: "Anual",
    resumo: "O menor custo por mês para quem já decidiu automatizar.",
    preco: 1497,
    meses: 12,
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

const brlMensal = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  minimumFractionDigits: 2,
});

/** Economia (%) do plano em relação a pagar o mensal N vezes. */
function economiaPercentual(plano: Plano): number {
  const cheio = PLANO_MENSAL_PRECO * plano.meses;
  return Math.round((1 - plano.preco / cheio) * 100);
}

export const Route = createFileRoute("/")({
  ssr: false,
  head: () => ({
    meta: [
      { title: "El Capo AutoBot — o robô analisa e opera por você" },
      {
        name: "description",
        content:
          "Pare de perder horas olhando gráfico. O El Capo analisa o mercado e executa as operações na sua conta da corretora, no automático, todos os dias.",
      },
      { property: "og:title", content: "El Capo AutoBot — o robô analisa e opera por você" },
      {
        property: "og:description",
        content:
          "Pare de perder horas olhando gráfico. O El Capo analisa o mercado e executa as operações na sua conta da corretora, no automático, todos os dias.",
      },
    ],
  }),
  component: LandingPage,
});

/**
 * Landing page pública do El Capo (rota `/`).
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
      <div className="lp-atmosphere" aria-hidden="true">
        <div className="login-orb login-orb-a" />
        <div className="login-orb login-orb-b" />
        <div className="login-grid" />
        <div className="login-scanline" />
      </div>

      <div className="relative z-10">
        <LpHeader />
        <main>
          <LpHero />
          <LpStrip />
          <LpPain />
          <LpHowItWorks />
          <LpFeatures />
          <LpControl />
          <LpPlans />
          <LpFaq />
          <LpFinalCta />
        </main>
        <LpFooter />
      </div>
    </div>
  );
}

function LpHeader() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

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
          <a className="lp-nav-link" href="#como-funciona">
            Como funciona
          </a>
          <a className="lp-nav-link" href="#recursos">
            Recursos
          </a>
          <a className="lp-nav-link" href="#controle">
            Controle e risco
          </a>
          <a className="lp-nav-link" href="#planos">
            Planos
          </a>
          <a className="lp-nav-link" href="#faq">
            Dúvidas
          </a>
        </nav>

        <div className="flex items-center gap-2">
          <Link to="/login" className="lp-btn-ghost hidden sm:inline-flex">
            Entrar
          </Link>
          <a href="#planos" className="lp-btn-primary lp-btn-sm">
            Ver planos
            <ArrowRight className="h-4 w-4" />
          </a>
        </div>
      </div>
    </header>
  );
}

function LpHero() {
  return (
    <section id="topo" className="lp-container pt-10 pb-16 sm:pt-16 lg:pt-20 lg:pb-24">
      <div className="grid items-center gap-12 lg:grid-cols-[1.05fr_0.95fr] lg:gap-16">
        <div className="lp-rise text-center lg:text-left">
          <span className="lp-eyebrow">
            <Sparkles className="h-3.5 w-3.5" />
            Robô de operações automáticas
          </span>

          <h1 className="lp-h1 mt-6">
            Não perca tempo analisando gráficos.
            <span className="lp-h1-accent"> Deixe o El Capo analisar e operar por você.</span>
          </h1>

          <p className="lp-lead mt-6">
            Todos os dias, no automático. Você diz quanto quer entrar e até onde quer ir — o robô lê
            o mercado vela a vela, escolhe a hora de entrar e executa direto na sua conta da
            corretora. Você só acompanha.
          </p>

          <div className="mt-8 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-center lg:justify-start">
            <a href="#planos" className="lp-btn-primary lp-btn-lg">
              Quero ativar meu robô
              <ArrowRight className="lp-btn-arrow h-5 w-5" />
            </a>
            <Link to="/login" className="lp-btn-outline lp-btn-lg">
              Já sou cliente
            </Link>
          </div>

          <ul className="lp-hero-trust mt-7">
            <li>
              <CheckCircle2 className="h-4 w-4 text-primary" />
              Conecta na sua conta da corretora
            </li>
            <li>
              <CheckCircle2 className="h-4 w-4 text-primary" />
              Stop win e stop loss no seu comando
            </li>
            <li>
              <CheckCircle2 className="h-4 w-4 text-primary" />
              Liga e desliga quando você quiser
            </li>
          </ul>
        </div>

        <div className="lp-rise-delayed">
          <LpRobotCard />
        </div>
      </div>
    </section>
  );
}

function LpRobotCard() {
  return (
    <div className="lp-robot-card">
      <div className="lp-robot-head">
        <span className="lp-live-dot" aria-hidden="true" />
        <span className="text-xs font-semibold uppercase tracking-[0.2em] text-primary/90">
          El Capo em operação
        </span>
      </div>

      <div className="lp-robot-stage">
        <RobotAvatarVideo
          className="lp-robot-video"
          aria-label="Robô El Capo analisando o mercado"
        />
      </div>

      <p className="lp-robot-caption">“Analisando o mercado… achei o ponto. Entrando agora.”</p>

      <div className="lp-robot-rows">
        <div className="lp-robot-row">
          <LineChart className="h-4 w-4 text-primary" />
          <span>Varredura dos ativos a cada vela</span>
          <span className="lp-robot-tag">automático</span>
        </div>
        <div className="lp-robot-row">
          <Zap className="h-4 w-4 text-primary" />
          <span>Entrada executada na corretora</span>
          <span className="lp-robot-tag">segundos</span>
        </div>
        <div className="lp-robot-row">
          <ShieldCheck className="h-4 w-4 text-primary" />
          <span>Para sozinho no seu limite do dia</span>
          <span className="lp-robot-tag">seu comando</span>
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
  "Cada dia uma estratégia diferente vista em vídeo",
  "Terminar o dia cansado e sem saber o que deu certo",
];

const PAIN_AFTER = [
  "O robô assiste ao gráfico por você, o dia inteiro",
  "Regra fria: entra quando o critério bate, não quando dá vontade",
  "Opera enquanto você trabalha, dirige ou dorme",
  "Uma única estratégia, sempre executada do mesmo jeito",
  "Placar do dia na tela: quantas entradas, o resultado de cada uma",
];

function LpPain() {
  return (
    <section className="lp-container lp-section">
      <div className="lp-section-head">
        <span className="lp-eyebrow">
          <TimerReset className="h-3.5 w-3.5" />O que muda no seu dia
        </span>
        <h2 className="lp-h2 mt-5">Você não precisa virar analista para operar todos os dias</h2>
        <p className="lp-section-sub mt-4">
          O gráfico não pede licença para dar sinal. O El Capo existe justamente para tirar essa
          vigilância das suas costas.
        </p>
      </div>

      <div className="mt-12 grid gap-5 lg:grid-cols-2">
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
    </section>
  );
}

const STEPS = [
  {
    icon: CreditCard,
    title: "Escolha seu plano",
    text: "Mensal, trimestral ou anual. Pagamento aprovado, o acesso ao painel é liberado e você já entra com seu e-mail e senha.",
  },
  {
    icon: Gauge,
    title: "Conecte a corretora e ajuste",
    text: "Você liga a sua conta da corretora ao painel e define valor de entrada, stop win e stop loss. Em dois minutos o robô sabe até onde pode ir no seu lugar.",
  },
  {
    icon: Play,
    title: "Aperte iniciar e pronto",
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
        <h2 className="lp-h2 mt-5">Três passos e o robô assume o gráfico</h2>
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
    </section>
  );
}

const FEATURES = [
  {
    icon: LineChart,
    title: "Leitura do mercado vela a vela",
    text: "O robô varre os ativos liberados a cada nova vela e só considera entrada quando o critério da estratégia bate. Sem palpite, sem ansiedade.",
  },
  {
    icon: Zap,
    title: "Entrada executada sozinho",
    text: "Do sinal à ordem na corretora são segundos. Você não precisa estar com o celular na mão para não perder a operação.",
  },
  {
    icon: Gauge,
    title: "Stop win e stop loss automáticos",
    text: "Bateu o ganho do dia ou o limite de perda, o robô encerra a sessão. O limite é seu — ele só obedece.",
  },
  {
    icon: BarChart3,
    title: "Placar em tempo real",
    text: "Entradas do dia, acertos, erros e resultado aparecem na tela enquanto acontece. Nada de planilha no fim do dia.",
  },
  {
    icon: CalendarClock,
    title: "Histórico dia a dia",
    text: "Todas as operações ficam registradas com data e resultado, para você olhar a semana inteira com calma.",
  },
  {
    icon: Smartphone,
    title: "Funciona no celular",
    text: "O painel abre no navegador do seu telefone. Ligou o robô, pode guardar o aparelho e tocar a sua vida.",
  },
];

function LpFeatures() {
  return (
    <section id="recursos" className="lp-container lp-section">
      <div className="lp-section-head">
        <span className="lp-eyebrow">
          <Sparkles className="h-3.5 w-3.5" />O que vem junto
        </span>
        <h2 className="lp-h2 mt-5">Tudo o que você faria na mão — feito por ele</h2>
      </div>

      <div className="mt-12 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map(({ icon: Icon, title, text }) => (
          <article key={title} className="lp-feature">
            <span className="lp-feature-icon">
              <Icon className="h-5 w-5" />
            </span>
            <h3 className="mt-5 text-base font-bold text-foreground">{title}</h3>
            <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{text}</p>
          </article>
        ))}
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
              da estratégia — nenhum robô garante lucro. Opere sempre com um valor que você pode
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
          É o mesmo robô, com tudo liberado, nos três planos. O que muda é o tempo de acesso — e
          quanto mais tempo, menor fica o custo por mês.
        </p>
      </div>

      <div className="mt-12 grid items-start gap-5 lg:grid-cols-3">
        {PLANOS.map((plano) => {
          const economia = economiaPercentual(plano);
          const porMes = plano.preco / plano.meses;

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
                <span className="lp-plan-amount">{brl.format(plano.preco)}</span>
                <span className="lp-plan-cycle">
                  {plano.meses === 1 ? "por mês" : `a cada ${plano.meses} meses`}
                </span>
              </div>

              {plano.meses > 1 ? (
                <p className="lp-plan-permonth">
                  Equivale a <strong>{brlMensal.format(porMes)}</strong> por mês
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

              <Link
                to="/register"
                className={`${plano.destaque ? "lp-btn-primary" : "lp-btn-outline"} lp-btn-lg mt-auto w-full`}
              >
                <Rocket className="h-4 w-4" />
                Assinar o {plano.nome.toLowerCase()}
              </Link>
            </article>
          );
        })}
      </div>

      <div className="lp-plan-note">
        <ShieldCheck className="h-5 w-5 shrink-0 text-primary" />
        <p>
          Pagamento por cartão ou Pix em ambiente seguro. Você tem <strong>7 dias</strong> para
          pedir reembolso a partir da compra, como manda o Código de Defesa do Consumidor — se o
          robô não for para você, é só avisar.
        </p>
      </div>
    </section>
  );
}

const FAQ = [
  {
    q: "Preciso saber analisar gráfico?",
    a: "Não. Essa é justamente a ideia: a análise é do robô. Você só precisa configurar o valor de entrada e os seus limites do dia.",
  },
  {
    q: "Preciso ficar olhando a tela?",
    a: "Não. Depois de iniciar a sessão, o robô segue operando sozinho. O painel fica disponível se você quiser acompanhar, mas não precisa ficar em cima.",
  },
  {
    q: "Onde as operações acontecem?",
    a: "Na sua própria conta da corretora, que você conecta ao painel. O saldo é seu e fica lá — o robô apenas executa as entradas.",
  },
  {
    q: "Posso parar quando quiser?",
    a: "Sim. Existe um botão de parar no painel e o robô encerra a sessão na hora. Ele também para sozinho quando bate o stop win ou o stop loss que você definiu.",
  },
  {
    q: "O robô garante lucro?",
    a: "Não, e desconfie de quem promete isso. O mercado tem risco e existem dias negativos. O que o El Capo garante é disciplina: a mesma estratégia, executada do mesmo jeito, sem emoção e sem cansaço.",
  },
  {
    q: "Como faço para começar?",
    a: "Escolha um dos planos nesta página e finalize o pagamento. Com o acesso liberado, é só entrar no painel, conectar a corretora, configurar seus limites e iniciar.",
  },
  {
    q: "Quanto preciso ter na corretora para operar?",
    a: "O plano dá acesso ao robô; a banca com que ele vai operar é sua e fica na corretora. Comece com um valor de entrada pequeno, veja o robô rodando e aumente quando estiver confortável.",
  },
  {
    q: "Posso trocar de plano ou cancelar?",
    a: "Pode. Você pode migrar para um plano maior quando quiser e cancelar a renovação a qualquer momento — o acesso segue ativo até o fim do período já pago.",
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
          Enquanto você lê isso, o mercado abriu mais uma vela.
        </h2>
        <p className="lp-section-sub mx-auto mt-4 max-w-2xl">
          Crie sua conta, conecte a corretora e deixe o El Capo assumir a parte chata. Você cuida da
          sua vida — ele cuida do gráfico.
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
          Já tem conta?{" "}
          <Link to="/login" className="lp-inline-link">
            Entrar no painel
          </Link>
        </p>
      </div>
    </section>
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
