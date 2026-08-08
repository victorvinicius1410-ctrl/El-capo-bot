import {
  createContext,
  useContext,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

interface MarketingPanelContextValue {
  open: boolean;
  setOpen: Dispatch<SetStateAction<boolean>>;
}

const MarketingPanelContext = createContext<MarketingPanelContextValue | null>(null);

/**
 * Expõe o estado aberto/fechado do painel Shift+O para páginas como Histórico.
 */
export function MarketingPanelProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const value = useMemo(() => ({ open, setOpen }), [open]);
  return <MarketingPanelContext.Provider value={value}>{children}</MarketingPanelContext.Provider>;
}

/**
 * Lê o contexto do painel marketing; retorna fechado fora do provider.
 */
export function useMarketingPanel(): MarketingPanelContextValue {
  return useContext(MarketingPanelContext) ?? { open: false, setOpen: () => undefined };
}
