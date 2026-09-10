"""Baldes dos projetos: falha/cancelamento saem da fila ativa."""

import pytest

from aula_uploader.upload_jobs import job_bucket, job_fase, job_tem_falha


def _job(**kw):
    base = {"status": "done", "items": [{"status": "ok"}]}
    base.update(kw)
    return base


# --------------------------------------------------------------- job_tem_falha


def test_sem_falha_quando_tudo_deu_certo():
    assert job_tem_falha(_job()) is False


def test_falha_quando_algum_item_falhou():
    assert job_tem_falha(
        _job(status="done_with_errors", items=[{"status": "ok"}, {"status": "falhou"}])
    ) is True


def test_falha_quando_o_job_inteiro_parou():
    assert job_tem_falha(_job(status="error", items=[{"status": "pendente"}])) is True


def test_cancelar_nao_e_falha():
    """Cancelar é escolha da pessoa, não erro — não fica pendente na fila."""
    assert job_tem_falha(_job(status="cancelado")) is False


def test_job_vazio_nao_e_falha():
    assert job_tem_falha({}) is False


# ------------------------------------------------------------------ job_bucket


@pytest.mark.parametrize(
    "job, esperado",
    [
        (_job(), "concluido"),
        (
            _job(status="done_with_errors", items=[{"status": "ok"}, {"status": "falhou"}]),
            "concluido",
        ),
        (_job(status="error", items=[{"status": "pendente"}]), "concluido"),
        (_job(status="cancelado"), "concluido"),
        (_job(status="running", items=[{"status": "enviando"}]), "andamento"),
    ],
)
def test_bucket(job, esperado):
    assert job_bucket(job) == esperado


def test_arquivar_vence_a_falha():
    """Se a pessoa arquivou de propósito, some da fila mesmo com falha."""
    job = _job(archived=True, status="error", items=[{"status": "falhou"}])
    assert job_bucket(job) == "historico"


def test_falha_vai_para_concluido():
    """Falha/erro saem da fila ativa; a fase continua descrevendo o estado."""
    job = _job(status="error", items=[{"status": "falhou"}])
    assert job_fase(job) == "error"
    assert job_bucket(job) == "concluido"
