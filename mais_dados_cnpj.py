"""Enriquece os CNPJs de uma planilha exportada em CSV com dados da BrasilAPI."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path
from typing import Literal

Status = Literal["ok", "cnpj_invalido", "nao_encontrado", "erro_consulta"]
Tipo = Literal["vazio", "cpf", "cnpj", "cnpj_invalido"]

RAIZ = Path(__file__).resolve().parent
PASTA_ENTRADA = RAIZ / "entrada"
ARQUIVO_CACHE = RAIZ / "cache" / "consultas.jsonl"
PASTA_SAIDA_PADRAO = Path(
    r"G:\.shortcut-targets-by-id\1fTi4B-tuKmtpIvbPypAQdhZmoPHpYoN0"
    r"\B&A - Perícia\T.I\csv_consolidados"
)

URL_API = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
USER_AGENT = "cnpj-lookup-script/1.0"
TENTATIVAS = 4
ESPERA_MAXIMA = 60.0
STATUS_FINAIS: frozenset[Status] = frozenset({"ok", "nao_encontrado"})
COLUNAS_DOCUMENTO = frozenset({"cpfcnpj", "cpfcnpjs", "cnpj", "cnpjs"})

ESTADOS = {
    "AC": "ACRE",
    "AL": "ALAGOAS",
    "AP": "AMAPA",
    "AM": "AMAZONAS",
    "BA": "BAHIA",
    "CE": "CEARA",
    "DF": "DISTRITO FEDERAL",
    "ES": "ESPIRITO SANTO",
    "GO": "GOIAS",
    "MA": "MARANHAO",
    "MT": "MATO GROSSO",
    "MS": "MATO GROSSO DO SUL",
    "MG": "MINAS GERAIS",
    "PA": "PARA",
    "PB": "PARAIBA",
    "PR": "PARANA",
    "PE": "PERNAMBUCO",
    "PI": "PIAUI",
    "RJ": "RIO DE JANEIRO",
    "RN": "RIO GRANDE DO NORTE",
    "RS": "RIO GRANDE DO SUL",
    "RO": "RONDONIA",
    "RR": "RORAIMA",
    "SC": "SANTA CATARINA",
    "SP": "SAO PAULO",
    "SE": "SERGIPE",
    "TO": "TOCANTINS",
}

_NAO_ALFANUMERICO = re.compile(r"[^0-9A-Za-z]")
_FORMATO_CNPJ = re.compile(r"[0-9A-Z]{12}[0-9]{2}")


class ErroEntrada(Exception):
    """Problema nos arquivos ou argumentos informados pelo usuário."""


@dataclass(frozen=True)
class Linha:
    cnpj: str
    razao_social: str = ""
    nome_fantasia: str = ""
    endereco: str = ""
    cidade: str = ""
    estado: str = ""
    cep: str = ""
    status: Status = "ok"


COLUNAS_SAIDA = [campo.name for campo in fields(Linha)]

Progresso = Callable[[int, int, Linha], None]


@dataclass(frozen=True)
class Resumo:
    destino: Path
    linhas_lidas: int
    contagem: Counter[str]


def _digitos_verificadores(base: str) -> str:
    # ord(c) - 48 também cobre o CNPJ alfanumérico (letras valem 17 a 42).
    valores = [ord(caractere) - 48 for caractere in base]
    for _ in range(2):
        soma = sum(v * (2 + i % 8) for i, v in enumerate(reversed(valores)))
        resto = soma % 11
        valores.append(0 if resto < 2 else 11 - resto)
    return f"{valores[-2]}{valores[-1]}"


def cnpj_valido(cnpj: str) -> bool:
    if not _FORMATO_CNPJ.fullmatch(cnpj) or len(set(cnpj)) == 1:
        return False
    return cnpj[12:] == _digitos_verificadores(cnpj[:12])


def classificar(valor: str) -> tuple[Tipo, str]:
    """Separa CPF de CNPJ e devolve o documento só com letras e dígitos."""
    bruto = valor.strip()
    chave = _NAO_ALFANUMERICO.sub("", bruto).upper()
    if not chave:
        return "vazio", ""
    if chave.isdigit():
        if len(chave) <= 11 and "/" not in bruto:
            return "cpf", chave
        # Células numéricas perdem os zeros à esquerda do CNPJ.
        chave = chave.zfill(14)
    return ("cnpj" if cnpj_valido(chave) else "cnpj_invalido"), chave


def formatar_cnpj(cnpj: str) -> str:
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def formatar_cep(cep: str) -> str:
    digitos = re.sub(r"\D", "", cep)
    if not digitos:
        return ""
    digitos = digitos.zfill(8)
    return f"{digitos[:5]}-{digitos[5:]}"


def montar_linha(cnpj: str, dados: Mapping[str, object]) -> Linha:
    def campo(nome: str) -> str:
        valor = dados.get(nome)
        return "" if valor is None else str(valor).strip()

    logradouro = " ".join(
        filter(None, (campo("descricao_tipo_de_logradouro"), campo("logradouro")))
    )
    uf = campo("uf").upper()
    return Linha(
        cnpj=formatar_cnpj(cnpj),
        razao_social=campo("razao_social"),
        nome_fantasia=campo("nome_fantasia"),
        endereco=", ".join(
            filter(None, (logradouro, campo("numero"), campo("bairro")))
        ),
        cidade=campo("municipio"),
        estado=ESTADOS.get(uf, uf),
        cep=formatar_cep(campo("cep")),
    )


def _espera(erro: urllib.error.HTTPError, tentativa: int) -> float:
    try:
        sugerida = float(erro.headers.get("Retry-After", ""))
    except ValueError:
        sugerida = 2.0 ** (tentativa + 1)
    return min(sugerida, ESPERA_MAXIMA)


def consultar_brasilapi(cnpj: str) -> Linha:
    requisicao = urllib.request.Request(
        URL_API.format(cnpj=cnpj),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    motivo = ""
    for tentativa in range(TENTATIVAS):
        espera = 2.0 ** (tentativa + 1)
        try:
            with urllib.request.urlopen(requisicao, timeout=30) as resposta:
                dados = json.load(resposta)
            if isinstance(dados, dict):
                return montar_linha(cnpj, dados)
            motivo = "resposta em formato inesperado"
        except urllib.error.HTTPError as erro:
            if erro.code == 404:
                return Linha(cnpj=formatar_cnpj(cnpj), status="nao_encontrado")
            motivo = f"HTTP {erro.code}"
            if erro.code != 429 and erro.code < 500:
                break
            espera = _espera(erro, tentativa)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as erro:
            motivo = str(erro)
        if tentativa < TENTATIVAS - 1:
            time.sleep(espera)
    print(f"  falha ao consultar {formatar_cnpj(cnpj)}: {motivo}", file=sys.stderr)
    return Linha(cnpj=formatar_cnpj(cnpj), status="erro_consulta")


def _so_letras(texto: str) -> str:
    return re.sub(r"[^a-z]", "", texto.casefold())


def localizar_coluna(cabecalho: Iterable[str], coluna: str | None) -> str:
    nomes = list(cabecalho)
    aceitas = {_so_letras(coluna)} if coluna else COLUNAS_DOCUMENTO
    for nome in nomes:
        if _so_letras(nome) in aceitas:
            return nome
    raise ErroEntrada(
        f"Coluna {coluna or 'CPF/CNPJ'!r} não encontrada. "
        f"Colunas disponíveis: {', '.join(nomes) or '(nenhuma)'}. "
        "Informe o nome correto com --coluna."
    )


def ler_documentos(arquivo: Path, coluna: str | None = None) -> list[str]:
    try:
        with arquivo.open(encoding="utf-8-sig", newline="") as origem:
            amostra = origem.readline()
            origem.seek(0)
            leitor = csv.DictReader(origem, delimiter=max(",;\t", key=amostra.count))
            nome = localizar_coluna(leitor.fieldnames or [], coluna)
            return [linha[nome] or "" for linha in leitor]
    except UnicodeDecodeError as erro:
        raise ErroEntrada(
            f"{arquivo.name} não está em UTF-8. Exporte pelo Google Sheets: "
            "Arquivo > Fazer download > Valores separados por vírgula (.csv)."
        ) from erro
    except OSError as erro:
        raise ErroEntrada(f"Não foi possível ler {arquivo}: {erro}") from erro


def entrada_mais_recente() -> Path:
    arquivos = sorted(PASTA_ENTRADA.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    if not arquivos:
        raise ErroEntrada(
            f"Nenhum .csv em {PASTA_ENTRADA}. No Google Sheets use Arquivo > "
            "Fazer download > Valores separados por vírgula (.csv) e salve nessa pasta."
        )
    return arquivos[-1]


def carregar_cache(arquivo: Path) -> dict[str, Linha]:
    cache: dict[str, Linha] = {}
    if not arquivo.exists():
        return cache
    with arquivo.open(encoding="utf-8") as origem:
        for texto in origem:
            # Uma execução interrompida pode deixar a última linha pela metade.
            try:
                registro = json.loads(texto)
                cache[registro["chave"]] = Linha(**registro["linha"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return cache


def imprimir_progresso(posicao: int, total: int, linha: Linha) -> None:
    print(f"[{posicao}/{total}] {linha.cnpj} {linha.status}")


def processar(
    documentos: Iterable[str],
    consultar: Callable[[str], Linha],
    cache: Mapping[str, Linha],
    salvar: Callable[[str, Linha], None],
    intervalo: float = 0.0,
    limite: int | None = None,
    ao_progredir: Progresso = imprimir_progresso,
) -> tuple[list[Linha], Counter[str]]:
    contagem: Counter[str] = Counter()
    candidatos: dict[str, Linha | None] = {}
    for valor in documentos:
        tipo, chave = classificar(valor)
        if tipo == "vazio" or tipo == "cpf":
            contagem[tipo] += 1
        elif chave in candidatos:
            contagem["repetido"] += 1
        elif tipo == "cnpj_invalido":
            candidatos[chave] = Linha(cnpj=valor.strip(), status="cnpj_invalido")
        else:
            candidatos[chave] = None

    itens = list(candidatos.items())[:limite]
    linhas: list[Linha] = []
    consultou = False
    for posicao, (chave, pronta) in enumerate(itens, start=1):
        linha = pronta or cache.get(chave)
        if linha is None:
            if consultou:
                time.sleep(intervalo)
            linha = consultar(chave)
            consultou = True
            if linha.status in STATUS_FINAIS:
                salvar(chave, linha)
        contagem[linha.status] += 1
        linhas.append(linha)
        ao_progredir(posicao, len(itens), linha)
    return linhas, contagem


def gravar_csv(linhas: Iterable[Linha], destino: Path) -> None:
    # utf-8-sig: o Excel só reconhece acentos em CSV UTF-8 quando há BOM.
    with destino.open("w", encoding="utf-8-sig", newline="") as saida:
        escritor = csv.DictWriter(saida, fieldnames=COLUNAS_SAIDA)
        escritor.writeheader()
        escritor.writerows(asdict(linha) for linha in linhas)


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument(
        "entrada",
        nargs="?",
        type=Path,
        help="CSV exportado da planilha (padrão: o .csv mais recente em entrada/)",
    )
    analisador.add_argument("--coluna", help="nome da coluna com os documentos")
    analisador.add_argument(
        "--saida",
        type=Path,
        default=PASTA_SAIDA_PADRAO,
        help="pasta onde o CSV final é salvo (padrão: csv_consolidados no Drive)",
    )
    analisador.add_argument(
        "--intervalo",
        type=float,
        default=0.5,
        help="segundos entre consultas (padrão: 0.5)",
    )
    analisador.add_argument(
        "--limite", type=int, help="consulta só os N primeiros CNPJs"
    )
    analisador.add_argument(
        "--reconsultar",
        action="store_true",
        help="ignora o cache e consulta todos os CNPJs de novo",
    )
    return analisador.parse_args(argv)


def executar(
    entrada: Path,
    saida: Path,
    *,
    coluna: str | None = None,
    intervalo: float = 0.5,
    limite: int | None = None,
    reconsultar: bool = False,
    ao_progredir: Progresso = imprimir_progresso,
) -> Resumo:
    documentos = ler_documentos(entrada, coluna)
    if not saida.is_dir():
        raise ErroEntrada(
            f"Pasta de saída não encontrada: {saida}. Abra o Google Drive "
            "para Desktop ou escolha outra pasta."
        )

    cache = {} if reconsultar else carregar_cache(ARQUIVO_CACHE)
    ARQUIVO_CACHE.parent.mkdir(exist_ok=True)
    with ARQUIVO_CACHE.open("a", encoding="utf-8") as registro:

        def salvar(chave: str, linha: Linha) -> None:
            dados = {"chave": chave, "linha": asdict(linha)}
            registro.write(json.dumps(dados, ensure_ascii=False) + "\n")
            registro.flush()

        linhas, contagem = processar(
            documentos,
            consultar_brasilapi,
            cache,
            salvar,
            intervalo,
            limite,
            ao_progredir,
        )

    destino = saida / f"empresas_brasilapi_{date.today():%Y-%m-%d}.csv"
    try:
        gravar_csv(linhas, destino)
    except OSError as erro:
        raise ErroEntrada(
            f"Não foi possível gravar {destino}: {erro}. As consultas estão "
            "salvas no cache; corrija a pasta de saída e execute de novo."
        ) from erro
    return Resumo(destino, len(documentos), contagem)


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    try:
        resumo = executar(
            args.entrada or entrada_mais_recente(),
            args.saida,
            coluna=args.coluna,
            intervalo=args.intervalo,
            limite=args.limite,
            reconsultar=args.reconsultar,
        )
    except ErroEntrada as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(
            "\nInterrompido. As consultas já feitas estão no cache; "
            "execute de novo para continuar.",
            file=sys.stderr,
        )
        return 130

    contagem = resumo.contagem
    print(
        f"\nLinhas lidas: {resumo.linhas_lidas} | CPFs ignorados: {contagem['cpf']} | "
        f"vazias: {contagem['vazio']} | CNPJs repetidos: {contagem['repetido']}"
    )
    print(" | ".join(f"{nome}: {contagem[nome]}" for nome in COLUNAS_STATUS))
    print(f"CSV salvo em: {resumo.destino}")
    if contagem["erro_consulta"]:
        print("Execute de novo para repetir só as consultas com erro.")
        return 1
    return 0


COLUNAS_STATUS: tuple[Status, ...] = (
    "ok",
    "nao_encontrado",
    "cnpj_invalido",
    "erro_consulta",
)

if __name__ == "__main__":
    sys.exit(main())
