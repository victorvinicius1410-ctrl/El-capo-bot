"""Module for Bullex websocket."""
import bullexapi.global_value as global_value

def profile(api, message):
    if message["name"] == "profile":
        api.profile.msg = message["msg"]
        if api.profile.msg != False:
            # ---------------------------
            try:
                api.profile.balance = message["msg"]["balance"]
            except:
                pass
            # Set Default account — só a sessão dona do runtime global pode
            # gravar balance_id. Sem isso, sob alta demanda o WS de outro
            # usuário preenche o id enquanto o login ativo ainda espera
            # balance_id=None → get_balance_mode() quebra e o painel cai em
            # REAL_BALANCE_NOT_DETECTED.
            owner = getattr(global_value, "balance_id_owner", None)
            if owner in (None, id(api)):
                if global_value.balance_id == None:
                    for balance in message["msg"]["balances"]:
                        if balance["type"] == 4:
                            global_value.balance_id = balance["id"]
                            break
            try:
                api.profile.balance_id = message["msg"]["balance_id"]
            except:
                pass

            try:
                api.profile.balance_type = message["msg"]["balance_type"]
            except:
                pass

            try:
                api.profile.balances = message["msg"]["balances"]
            except:
                pass
