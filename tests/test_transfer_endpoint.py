from types import SimpleNamespace

from services.soma_api_service import SomaApiService


class _Resp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text
        self.url = ""

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _Http:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post_ajax(self, url, data=None, **kw):
        self.calls.append(url)
        return self.resp

    def post(self, url, data=None, **kw):
        raise AssertionError("transferência deve usar post_ajax, não post")


def _service(resp, transfers_before=(), transfers_after=()):
    settings = SimpleNamespace(site_base_url="https://x.test/IVV/", institution_id="1", user_job_id="j")
    http = _Http(resp)
    svc = SomaApiService(settings, http)
    svc.resolve_caixa_id = lambda name: "1230" if "DI" in name else "1226"
    seq = iter([list(transfers_before), list(transfers_after)])
    svc.find_transfers = lambda row: (next(seq), [])
    svc._confirmation_attempts = 1
    return svc, http


def _row():
    return SimpleNamespace(row_number=3752, importancia="520,00", data_mov="05/11/2026",
                           caixa_saida="CAIXA DIÁRIO", caixa="CAIXA ECONÓMICA", descricao_soma="", descricao="X")


def test_transferencia_posta_no_endpoint_ajax_do_formulario():
    svc, http = _service(_Resp(body={"status": 11}), )
    svc.find_transfers = lambda row: ([], [])
    svc.criar_transferencia(_row())
    assert http.calls == ["https://x.test/IVV/sys/app/transferencias_caixas.php"]


def test_status_diferente_de_1_falha_com_mensagem():
    svc, _ = _service(_Resp(body={"status": 5}))
    svc.find_transfers = lambda row: ([], [])
    out = svc.criar_transferencia(_row())
    assert not out.success
    assert "valor solicitado" in out.error_message


def test_resposta_nao_json_falha():
    svc, _ = _service(_Resp(body=None, text="<html>dashboard</html>"))
    svc.find_transfers = lambda row: ([], [])
    out = svc.criar_transferencia(_row())
    assert not out.success
