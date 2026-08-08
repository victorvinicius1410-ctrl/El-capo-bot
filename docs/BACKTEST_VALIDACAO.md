# Validação da estratégia com candles reais (backtest walk-forward)

Documento criado em **2026-07-29** para registrar como medir se uma mudança na
análise do El Capo melhora ou piora o resultado **antes** de subir para
produção. Até aqui as decisões de estratégia eram avaliadas apenas por
inspeção de código e pelos poucos trades do dia, amostra pequena demais para
distinguir sorte de vantagem real.

## 1. Por que existe

Em opções binárias o resultado é assimétrico: com payout de 88% é preciso
acertar **53,2%** das entradas só para empatar.

```
acerto de empate = 100 / (1 + payout/100)
payout 88%  -> 53,2%
payout 85%  -> 54,1%
payout 90%  -> 52,6%
```

Uma estratégia com 51% de acerto "parece funcionar" (ganha e perde alternando)
mas perde dinheiro de forma consistente. Um punhado de operações no dia não
distingue 51% de 55%; por isso a validação usa centenas de entradas.

## 2. Metodologia (walk-forward, sem look-ahead)

Para cada ativo e cada vela `i` do histórico:

1. Monta a série **fechada** `candles[:i]` (a vela de entrada nunca entra na
   análise — é exatamente o que o robô vê ao decidir).
2. Chama `analyze_signal(...)` com o timeframe, o perfil e o payout desejados.
3. Aplica o mesmo portão do usuário: `confidence >= min_confidence` e
   `trade_allowed == True`.
4. Simula o resultado da entrada na vela `i`: CALL ganha se `close > open`,
   PUT ganha se `close < open` (equivale a entrar nos primeiros segundos da
   vela com expiração no fim dela, que é a janela real de compra).
5. Acumula acerto e P/L em stakes unitários (`+payout/100` na vitória, `−1` na
   derrota).

O módulo `backend/backtesting.py` (`run_walk_forward_backtest`) implementa esse
laço para uso em testes. Para medições exploratórias por timeframe, perfil ou
filtro, é mais prático rodar o laço direto num script, como abaixo.

### Limitações conhecidas

| Limitação | Efeito |
|---|---|
| Resultado aproximado por `open`/`close` da vela | Ignora o preço exato de execução (alguns décimos de pip) |
| Sem slippage nem indisponibilidade de ativo | Superestima levemente a frequência de entradas |
| Payout fixo | O payout real varia por ativo e horário |
| Histórico OTC recente (≈1000 velas por ativo) | Mede o regime **atual** do mercado, não um ciclo longo |

As três primeiras afetam os cenários comparados **do mesmo jeito**, então a
comparação relativa (com filtro novo vs sem) continua válida.

## 3. Como rodar

Os candles vêm do `bullex-service`, que exige uma sessão conectada de usuário.
O container de teste precisa da rede do compose para alcançá-lo.

```bash
# 1. Descobrir a rede e um user_id com sessão ativa
docker inspect backend-gateway --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'

# 2. Buscar candles (interval em segundos: 60=M1, 300=M5, 900=M15)
docker exec -i backend-gateway python -c "
import httpx
r = httpx.get('http://bullex-service:8000/candles',
              params={'active':'EURUSD-OTC','interval':60,'count':1000},
              headers={'x-user-id':'<USER_ID>'}, timeout=30)
print(r.status_code, r.text[:200])
"

# 3. Rodar o script de medição com o código do repositório montado
docker run --rm --network elcapooneline_default \
  -v /root/Backend:/src -w /src -e PYTHONPATH=/src \
  -v /tmp/validate.py:/tmp/validate.py \
  elcapooneline-backend python /tmp/validate.py
```

Esqueleto do script de medição:

```python
import httpx
from backend import signal_engine
from backend.signal_engine import analyze_signal

UID = "<USER_ID>"
ASSETS = ["EURUSD-OTC", "GBPUSD-OTC", "USDCAD-OTC", "AUDJPY-OTC",
          "USDCHF-OTC", "GBPJPY-OTC", "USDJPY-OTC", "EURGBP-OTC"]
MIN_HISTORY, PAYOUT, MIN_CONF = 60, 88.0, 80

series = {}
for active in ASSETS:
    response = httpx.get("http://bullex-service:8000/candles",
                         params={"active": active, "interval": 60, "count": 1000},
                         headers={"x-user-id": UID}, timeout=60)
    body = response.json()
    series[active] = (body.get("data") or []) if body.get("ok") else []

def measure(label: str, hard_block: bool) -> None:
    signal_engine.SR_ZONE_HARD_BLOCK = hard_block  # a flag em avaliação
    wins = losses = 0
    for active, candles in series.items():
        for index in range(MIN_HISTORY, len(candles)):
            closed, entry = candles[:index], candles[index]
            signal = analyze_signal(active, closed, timeframe="M1",
                                    strategy_mode="balanced", payout=PAYOUT)
            if int(signal.get("confidence") or 0) < MIN_CONF:
                continue
            if not signal.get("trade_allowed"):
                continue
            direction = str(signal.get("signal") or "").upper()
            if direction not in {"CALL", "PUT"}:
                continue
            won = (direction == "CALL" and float(entry["close"]) > float(entry["open"])) or \
                  (direction == "PUT" and float(entry["close"]) < float(entry["open"]))
            wins += 1 if won else 0
            losses += 0 if won else 1
    total = wins + losses
    accuracy = 100 * wins / total if total else 0.0
    print(f"{label}: ops={total} acerto={accuracy:.1f}% "
          f"P/L={wins * PAYOUT / 100 - losses:+.2f}")

measure("antes", False)
measure("depois", True)
```

## 4. Como interpretar

| Sinal | Leitura |
|---|---|
| Acerto abaixo do empate | A configuração perde dinheiro; não subir |
| Acerto acima do empate com < 100 operações | Amostra fraca; ampliar ativos/velas antes de concluir |
| Filtro novo corta muitas entradas sem subir o acerto | Só reduz frequência; provavelmente não vale |
| Filtro novo corta poucas entradas e sobe o acerto | Bom candidato (caso do `SR_ZONE`) |

Compare **sempre** dois cenários na mesma amostra (antes/depois). Números
absolutos variam com o regime do mercado; a diferença entre cenários é o que
tem valor de decisão.

## 5. Resultados registrados

### 2026-07-29 — bloqueio `SR_ZONE` e edge por timeframe

Amostra: 8 ativos OTC × 1000 velas por timeframe, confiança ≥ 80, payout 88%
(empate 53,2%).

| Cenário | Operações | Acerto | P/L |
|---|---|---|---|
| M1 sem `SR_ZONE` | 228 | 53,9% | +3,24 |
| M1 com `SR_ZONE` | 226 | **54,4%** | **+5,24** |
| M5 com `SR_ZONE` | 279 | 48,7% | −23,32 |
| M15 com `SR_ZONE` | 334 | 50,9% | −14,40 |

Sem o portão de confiança do usuário, a estratégia cai para 50,7% em M1 — a
faixa de confiança 60–69 acerta apenas 46,2% e é a que mais destrói resultado.
Isso explica por que `min_confidence` ≥ 80 é o ajuste mais importante do painel.

Decisões tomadas a partir desses números: ativar `SR_ZONE` (custo de ~1% das
entradas) e recomendar **M1** como único timeframe com vantagem.
Ver [`ESTRATEGIA.md`](./ESTRATEGIA.md).

## 6. Cuidados

- O script **não** envia ordens: só lê candles e roda a análise. Ainda assim,
  usa a sessão de um usuário real para buscar candles — evite rodadas longas em
  paralelo com o robô operando, para não competir por rate limit.
- Nunca altere `/root/backup` para testar hipóteses; ele é a referência da
  estratégia clássica.
- Alterar flags de módulo (`signal_engine.SR_ZONE_HARD_BLOCK = ...`) dentro do
  script vale só para o processo do backtest; produção continua com o valor do
  código.
